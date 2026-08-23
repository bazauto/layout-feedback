"""PN7150 mux reader manager (T2T) with event callbacks.

Exposes PN7150MuxManager for use in main.py.
"""

from machine import I2C, Pin
from utime import sleep_ms, ticks_ms, ticks_diff

# NCI constants
MODE_POLL = 0x00
TECH_PASSIVE_NFCA = 0x00
PROT_T2T = 0x02
PROT_ISODEP = 0x04
PROT_MIFARE = 0x80
INTF_ISODEP = 0x02
INTF_TAGCMD = 0x80
INTF_FRAME = 0x01

STATE_INIT = 0
STATE_DISCOVERY = 1
STATE_ACTIVATING = 2
STATE_ACTIVE = 3
STATE_DEACTIVATING = 4

# Timing defaults
PRESENCE_INTERVAL_MS = 250
PRESENCE_TIMEOUT_MS = 200
PRESENCE_FAIL_LIMIT = 2
DISCOVER_RETRY_MS = 500


def _pick_interface(protocol: int) -> int:
    if protocol == PROT_ISODEP:
        return INTF_ISODEP
    if protocol in (PROT_MIFARE, PROT_T2T):
        return INTF_TAGCMD
    return INTF_FRAME


def _parse_uid_from_intf_activated(msg: bytes) -> bytes | None:
    if not msg or len(msg) < 6:
        return None
    payload_len = msg[2]
    if payload_len == 0:
        return None
    payload = msg[3:3 + payload_len]
    if len(payload) < 7:
        return None
    mode_tech = payload[3]
    rf_params_len = payload[6]
    rf_params = payload[7:7 + rf_params_len]
    if (mode_tech & 0x0F) != TECH_PASSIVE_NFCA:
        return None
    if len(rf_params) < 4:
        return None
    uid_len = rf_params[2]
    if uid_len == 0 or (3 + uid_len) > len(rf_params):
        return None
    return bytes(rf_params[3:3 + uid_len])


def _uid_hex(uid: bytes | None) -> str:
    if not uid:
        return ""
    return ":".join("%02X" % b for b in uid)


def _protocol_name(protocol: int) -> str:
    if protocol == PROT_T2T:
        return "T2T"
    if protocol == PROT_ISODEP:
        return "ISO-DEP"
    if protocol == PROT_MIFARE:
        return "MIFARE"
    return "0x%02X" % protocol


class I2CMux:
    def __init__(self, i2c: I2C, address: int):
        self._i2c = i2c
        self._address = address
        self._current = None

    def select(self, channel: int) -> None:
        if not 0 <= channel <= 7:
            raise ValueError("channel must be 0..7")
        if self._current == channel:
            return
        self._i2c.writeto(self._address, bytes([1 << channel]))
        self._current = channel


class PN7150NCI:
    def __init__(self, i2c: I2C, address: int, irq_pin: Pin, mux: I2CMux, mux_channel: int, irq_active_high: bool = True):
        self._i2c = i2c
        self._address = address
        self._irq_pin = irq_pin
        self._mux = mux
        self._mux_channel = mux_channel
        self._irq_active_high = irq_active_high

    def _select_mux(self) -> None:
        self._mux.select(self._mux_channel)

    def has_message(self) -> bool:
        expected = 1 if self._irq_active_high else 0
        return self._irq_pin.value() == expected

    def write(self, data: bytes) -> bool:
        try:
            self._select_mux()
            self._i2c.writeto(self._address, data)
            return True
        except OSError:
            return False

    def read_once(self) -> bytes | None:
        if not self.has_message():
            return None
        try:
            self._select_mux()
            header = self._i2c.readfrom(self._address, 3)
        except OSError:
            return None
        if not header or len(header) < 3:
            return None
        payload_len = header[2]
        if payload_len == 0:
            return header
        try:
            self._select_mux()
            payload = self._i2c.readfrom(self._address, payload_len)
        except OSError:
            return None
        return header + payload

    def read_wait(self, timeout_ms: int = 1000) -> bytes | None:
        start = ticks_ms()
        while not self.has_message():
            if ticks_diff(ticks_ms(), start) > timeout_ms:
                return None
            sleep_ms(2)
        return self.read_once()

    def core_reset(self) -> bool:
        return self.write(bytes([0x20, 0x00, 0x01, 0x01]))

    def core_init(self) -> bool:
        return self.write(bytes([0x20, 0x01, 0x00]))

    def rf_discover(self) -> bool:
        return self.write(bytes([0x21, 0x03, 0x03, 0x01, MODE_POLL | TECH_PASSIVE_NFCA, 0x01]))

    def rf_discover_select(self, discovery_id: int, protocol: int, interface: int) -> bool:
        return self.write(bytes([0x21, 0x04, 0x03, discovery_id & 0xFF, protocol & 0xFF, interface & 0xFF]))

    def rf_deactivate(self) -> bool:
        return self.write(bytes([0x21, 0x06, 0x01, 0x00]))

    def data_packet(self, payload: bytes) -> bool:
        length = len(payload)
        header = bytes([0x00, 0x00, length & 0xFF])
        return self.write(header + payload)


class PN7150ReaderSM:
    def __init__(self, name: str, topic: str, nci: PN7150NCI, on_event):
        self._name = name
        self._topic = topic
        self._nci = nci
        self._on_event = on_event
        self._state = STATE_INIT
        self._current_uid = None
        self._current_protocol = None
        self._last_discover_ms = 0
        self._next_presence_ms = 0
        self._presence_deadline_ms = 0
        self._presence_failures = 0
        self._pending_presence = False
        self._presence_ok_count = 0
        self._presence_fail_count = 0
        self._last_deactivate_reason = None

    def init(self) -> bool:
        if not self._nci.core_reset():
            return False
        msg = self._nci.read_wait(timeout_ms=500)
        if not msg or msg[0] != 0x40 or msg[1] != 0x00:
            return False

        extra = self._nci.read_wait(timeout_ms=200)
        if extra:
            pass

        if not self._nci.core_init():
            return False
        msg = self._nci.read_wait(timeout_ms=500)
        if not msg or msg[0] != 0x40 or msg[1] != 0x01:
            return False

        self._state = STATE_DISCOVERY
        self._last_discover_ms = 0
        return True

    def _emit_activated(self, uid: bytes, protocol: int) -> None:
        if self._on_event:
            self._on_event(self._name, self._topic, "activate", _uid_hex(uid), protocol, _protocol_name(protocol))

    def _emit_deactivated(self) -> None:
        if self._on_event:
            self._on_event(self._name, self._topic, "deactivate", "", self._current_protocol, _protocol_name(self._current_protocol or 0))

    def _send_discover(self) -> None:
        if self._nci.rf_discover():
            self._last_discover_ms = ticks_ms()

    def _send_presence_check(self) -> None:
        # T2T presence check: READ 0x30 page 0
        self._nci.data_packet(bytes([0x30, 0x00]))
        self._pending_presence = True
        self._presence_deadline_ms = ticks_ms() + self._nci_cfg["presence_timeout_ms"]

    def _handle_data_packet(self) -> None:
        if self._pending_presence:
            self._pending_presence = False
            self._presence_failures = 0
            self._next_presence_ms = ticks_ms() + self._nci_cfg["presence_interval_ms"]
            self._presence_ok_count = (self._presence_ok_count + 1) % 1000000

    def _handle_message(self, msg: bytes) -> None:
        if msg[0] == 0x61 and msg[1] == 0x03 and len(msg) >= 7:
            payload_len = msg[2]
            payload = msg[3:3 + payload_len]
            if len(payload) >= 4:
                discovery_id = payload[0]
                protocol = payload[1]
                interface = _pick_interface(protocol)
                self._nci.rf_discover_select(discovery_id, protocol, interface)
                self._state = STATE_ACTIVATING
            return

        if msg[0] == 0x61 and msg[1] == 0x05:
            uid = _parse_uid_from_intf_activated(msg)
            protocol = msg[5] if len(msg) > 5 else 0x00
            if uid:
                if uid != self._current_uid:
                    self._emit_activated(uid, protocol)
                self._current_uid = uid
                self._current_protocol = protocol
                self._state = STATE_ACTIVE
                self._next_presence_ms = ticks_ms() + self._nci_cfg["presence_interval_ms"]
                self._presence_failures = 0
                self._presence_ok_count = 0
                self._presence_fail_count = 0
            return

        if msg[0] == 0x61 and msg[1] == 0x06:
            if self._state in (STATE_ACTIVE, STATE_ACTIVATING):
                self._last_deactivate_reason = "RF_DEACTIVATE_NTF"
                self._state = STATE_DEACTIVATING
            return

        if msg[0] == 0x60 and msg[1] == 0x08:
            if self._state == STATE_ACTIVE:
                self._last_deactivate_reason = "CORE_INTERFACE_ERROR_NTF"
                self._state = STATE_DEACTIVATING
            return

        if msg[0] == 0x00:
            self._handle_data_packet()
            return

    def _process_irq_messages(self) -> None:
        while self._nci.has_message():
            msg = self._nci.read_once()
            if not msg:
                break
            self._handle_message(msg)

    def _step(self) -> None:
        now = ticks_ms()
        if self._state == STATE_DISCOVERY:
            if ticks_diff(now, self._last_discover_ms) > self._nci_cfg["discover_retry_ms"]:
                self._send_discover()

        elif self._state == STATE_ACTIVE:
            if self._pending_presence and ticks_diff(now, self._presence_deadline_ms) > 0:
                self._pending_presence = False
                self._presence_failures += 1
                self._presence_fail_count = (self._presence_fail_count + 1) % 1000000
                self._last_deactivate_reason = "presence_timeout"

            if ticks_diff(now, self._next_presence_ms) > 0 and not self._pending_presence:
                self._send_presence_check()

            if self._presence_failures >= self._nci_cfg["presence_fail_limit"]:
                self._last_deactivate_reason = "presence_fail_limit"
                self._state = STATE_DEACTIVATING

        if self._state == STATE_DEACTIVATING:
            self._emit_deactivated()
            self._current_uid = None
            self._current_protocol = None
            self._presence_failures = 0
            self._pending_presence = False
            self._last_deactivate_reason = None
            self._presence_ok_count = 0
            self._presence_fail_count = 0
            self._nci.rf_deactivate()
            self._state = STATE_DISCOVERY

    def loop(self) -> None:
        if self._nci.has_message():
            self._process_irq_messages()
        self._step()

    def set_config(self, config: dict) -> None:
        self._nci_cfg = config


class PN7150MuxManager:
    def __init__(self, i2c: I2C, mux_address: int, reader_configs: list[dict], on_event=None, irq_active_high: bool = True):
        self._i2c = i2c
        self._mux = I2CMux(i2c, mux_address)
        self._on_event = on_event
        self._irq_active_high = irq_active_high
        self._readers: list[PN7150ReaderSM] = []

        self._cfg = {
            "presence_interval_ms": PRESENCE_INTERVAL_MS,
            "presence_timeout_ms": PRESENCE_TIMEOUT_MS,
            "presence_fail_limit": PRESENCE_FAIL_LIMIT,
            "discover_retry_ms": DISCOVER_RETRY_MS,
        }

        for cfg in reader_configs:
            irq_pin = Pin(cfg["irq"], Pin.IN, Pin.PULL_DOWN if irq_active_high else Pin.PULL_UP)
            nci = PN7150NCI(i2c, cfg["i2c_address"], irq_pin, self._mux, cfg["channel"], irq_active_high)
            sm = PN7150ReaderSM(cfg["name"], cfg["topic"], nci, on_event)
            sm.set_config(self._cfg)
            self._readers.append(sm)

    def init(self) -> bool:
        ok = False
        for sm in self._readers:
            if sm.init():
                ok = True
        return ok

    def loop(self) -> None:
        for sm in self._readers:
            sm.loop()
