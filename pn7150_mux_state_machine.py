"""PN7150 multi-reader state machine with I2C mux (T2T), IRQ-driven.

Wiring (Raspberry Pi Pico / RP2040):
- I2C1 SDA -> GP2
- I2C1 SCL -> GP3
- PN7150 IRQ -> per-reader GPIO (active high)
"""

from machine import I2C, Pin
from utime import sleep_ms, ticks_ms, ticks_diff

I2C_ID = 1
I2C_SDA = 2
I2C_SCL = 3
I2C_FREQ = 100_000
I2C_ADDRESS = 0x28

MUX_I2C_ADDRESS = 0x70

IRQ_ACTIVE_HIGH = True

# Timing
PRESENCE_INTERVAL_MS = 250
PRESENCE_TIMEOUT_MS = 200
PRESENCE_FAIL_LIMIT = 2
DISCOVER_RETRY_MS = 500

# NCI constants
MODE_POLL = 0x00
TECH_PASSIVE_NFCA = 0x00
PROT_T2T = 0x02
PROT_ISODEP = 0x04
PROT_MIFARE = 0x80
INTF_ISODEP = 0x02
INTF_TAGCMD = 0x80
INTF_FRAME = 0x01

# State
STATE_INIT = 0
STATE_DISCOVERY = 1
STATE_ACTIVATING = 2
STATE_ACTIVE = 3
STATE_DEACTIVATING = 4


class I2CMux:
    def __init__(self, i2c: I2C, address: int = MUX_I2C_ADDRESS):
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
    def __init__(self, i2c: I2C, address: int, irq_pin: Pin, mux: I2CMux, mux_channel: int):
        self._i2c = i2c
        self._address = address
        self._irq_pin = irq_pin
        self._mux = mux
        self._mux_channel = mux_channel

    def _select_mux(self) -> None:
        self._mux.select(self._mux_channel)

    def has_message(self) -> bool:
        return self._irq_pin.value() == (1 if IRQ_ACTIVE_HIGH else 0)

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
        return "unknown"
    return ":".join("%02X" % b for b in uid)


def _ts() -> str:
    return "%dms" % ticks_ms()


def _protocol_name(protocol: int) -> str:
    if protocol == PROT_T2T:
        return "T2T"
    if protocol == PROT_ISODEP:
        return "ISO-DEP"
    if protocol == PROT_MIFARE:
        return "MIFARE"
    return "0x%02X" % protocol


class PN7150StateMachine:
    def __init__(self, name: str, nci: PN7150NCI):
        self._name = name
        self._nci = nci
        self._state = STATE_INIT
        self._current_uid = None
        self._current_protocol = None
        self._last_discover_ms = 0
        self._next_presence_ms = 0
        self._presence_deadline_ms = 0
        self._presence_failures = 0
        self._pending_presence = False
        self._last_deactivate_reason = None
        self._presence_ok_count = 0
        self._presence_fail_count = 0

    def init(self) -> bool:
        if not self._nci.core_reset():
            print(self._name, "CORE_RESET write failed")
            return False
        msg = self._nci.read_wait(timeout_ms=500)
        if not msg or msg[0] != 0x40 or msg[1] != 0x00:
            print(self._name, "CORE_RESET response invalid:", msg)
            return False

        extra = self._nci.read_wait(timeout_ms=200)
        if extra:
            print(self._name, "NTF:", extra)

        if not self._nci.core_init():
            print(self._name, "CORE_INIT write failed")
            return False
        msg = self._nci.read_wait(timeout_ms=500)
        if not msg or msg[0] != 0x40 or msg[1] != 0x01:
            print(self._name, "CORE_INIT response invalid:", msg)
            return False

        self._state = STATE_DISCOVERY
        self._last_discover_ms = 0
        return True

    def _emit_activated(self, uid: bytes, protocol: int) -> None:
        print(self._name, _ts(), "Activated:", _uid_hex(uid), "protocol:", _protocol_name(protocol))

    def _emit_deactivated(self) -> None:
        reason = self._last_deactivate_reason or "unknown"
        print(
            self._name,
            _ts(),
            "Deactivated:",
            _uid_hex(self._current_uid),
            "reason:",
            reason,
            "presence_ok:",
            self._presence_ok_count,
            "presence_fail:",
            self._presence_fail_count,
        )

    def _send_discover(self) -> None:
        if self._nci.rf_discover():
            self._last_discover_ms = ticks_ms()

    def _send_presence_check(self) -> None:
        self._nci.data_packet(bytes([0x30, 0x00]))
        self._pending_presence = True
        self._presence_deadline_ms = ticks_ms() + PRESENCE_TIMEOUT_MS

    def _handle_data_packet(self) -> None:
        if self._pending_presence:
            self._pending_presence = False
            self._presence_failures = 0
            self._next_presence_ms = ticks_ms() + PRESENCE_INTERVAL_MS
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
                self._next_presence_ms = ticks_ms() + PRESENCE_INTERVAL_MS
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
            if ticks_diff(now, self._last_discover_ms) > DISCOVER_RETRY_MS:
                self._send_discover()

        elif self._state == STATE_ACTIVE:
            if self._pending_presence and ticks_diff(now, self._presence_deadline_ms) > 0:
                self._pending_presence = False
                self._presence_failures += 1
                self._presence_fail_count = (self._presence_fail_count + 1) % 1000000
                self._last_deactivate_reason = "presence_timeout"

            if ticks_diff(now, self._next_presence_ms) > 0 and not self._pending_presence:
                self._send_presence_check()

            if self._presence_failures >= PRESENCE_FAIL_LIMIT:
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


def main() -> None:
    i2c = I2C(I2C_ID, sda=Pin(I2C_SDA), scl=Pin(I2C_SCL), freq=I2C_FREQ)
    mux = I2CMux(i2c, MUX_I2C_ADDRESS)

    reader_configs = [
        ("reader-0", 0, 16),
        ("reader-1", 1, 17),
    ]

    if len(reader_configs) > 8:
        print("Too many readers for a single mux (max 8).")
        return

    channels = [channel for _, channel, _ in reader_configs]
    if any(channel < 0 or channel > 7 for channel in channels):
        print("Invalid mux channel in reader_configs (must be 0..7).")
        return
    if len(set(channels)) != len(channels):
        print("Duplicate mux channels detected in reader_configs.")
        return

    sms: list[PN7150StateMachine] = []
    for name, channel, irq_gpio in reader_configs:
        irq_pin = Pin(irq_gpio, Pin.IN, Pin.PULL_DOWN if IRQ_ACTIVE_HIGH else Pin.PULL_UP)
        nci = PN7150NCI(i2c, I2C_ADDRESS, irq_pin, mux, channel)
        sm = PN7150StateMachine(name, nci)
        if sm.init():
            sms.append(sm)

    if not sms:
        print("No readers initialized.")
        return

    print("Waiting for NFC tags (mux state machine)...")
    while True:
        for sm in sms:
            sm.loop()
        sleep_ms(2)


if __name__ == "__main__":
    main()
