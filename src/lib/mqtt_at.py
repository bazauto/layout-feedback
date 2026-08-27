"""Simple AT-based MQTT client for MicroPython (UART-driven)

This module sends AT-style MQTT commands to an external modem (ESP/ESP32)
connected over a hardware UART. It provides a small API for input modules
to publish messages and for output modules to register topic handlers.

Usage:
    from mqtt_at import MQTTATClient

    client = MQTTATClient(uart_id=1, tx_pin=8, rx_pin=9, baud=115200)
    client.setup()
    client.set_base_config()  # sends a sensible default AT+MQTTUSERCFG
    client.connect('172.18.10.240', 1883)
    client.subscribe('track/#')

    # Register a handler for a topic (exact or prefix with '#')
    def on_track(line):
        # line is the raw AT response line from the modem
        print('RX:', line)

    client.register_topic_handler('track/', on_track)

    # In main loop call:
    #    client.poll()

Notes:
    - This is intentionally conservative about parsing modem responses.
      Registered handlers receive the raw response line and should parse
      topic/payload as appropriate for the modem/AT firmware in use.
    - `poll()` must be called frequently from the main loop to process
      incoming UART data and dispatch handlers.
"""

try:
    from machine import UART, Pin
except Exception:
    UART = None
    Pin = None

try:
    import time as _time
except Exception:
    import time as _time

# allow static type-checkers to see Optional without breaking MicroPython at runtime
try:
    from typing import Optional
except Exception:
    # typing may not be available on MicroPython; ignore at runtime
    pass

def _now_ms():
    # MicroPython provides ticks_ms; CPython doesn't — normalize to ms
    if hasattr(_time, 'ticks_ms'):
        return _time.ticks_ms()
    return int(_time.time() * 1000)

def _sleep_ms(ms):
    if hasattr(_time, 'sleep_ms'):
        _time.sleep_ms(ms)
    else:
        _time.sleep(ms / 1000.0)


# --- Frame reading ---------------------------------------------------------
#
# Everything the modem emits is a CRLF-terminated ASCII line, with one exception:
# `+MQTTSUBRECV` carries an arbitrary payload of a declared byte length, and that
# payload may itself contain CRLF or a double quote. Splitting the stream on CRLF
# therefore works for `ACTIVE`/`INACTIVE` and corrupts JSON — a payload containing a
# CRLF arrives as two lines, and the tail is then parsed as a bogus command.
#
# So the reader is length-aware: line-oriented for everything else, and byte-counted
# for a subscribe delivery. See #2.

_SUBRECV_PREFIX = b'+MQTTSUBRECV:'
_CRLF = b'\r\n'

RECONNECT_BACKOFF_MS = 5000

# How long to wait for the modem to start answering at all, and how long each probe
# gets. See `wait_for_modem()` for why asking first is not optional.
MODEM_READY_TIMEOUT_MS = 15000
MODEM_PROBE_INTERVAL_MS = 500


# --- startup failures ------------------------------------------------------
#
# These subclass `RuntimeError`, which is what `setup()` used to raise and what any
# existing caller already catches. The subclasses exist so a supervisor can tell *which*
# stage failed without matching on message text — the node flashes a different LED code
# for each, and on a headless board that code is the only diagnostic there is.

class ATError(RuntimeError):
    """Base for the transport's startup failures."""


class ModemNotResponding(ATError):
    """The modem is not answering AT commands at all."""


class NetworkNotReady(ATError):
    """The modem answers, but never reported an IP address."""


class BrokerUnreachable(ATError):
    """The network is up, but the broker link could not be established."""


def _take_line(buf):
    """Take one CRLF-terminated line, or None if it has not fully arrived."""
    idx = buf.find(_CRLF)
    if idx == -1:
        return None
    return ('line', buf[:idx].decode('ascii', 'replace'), idx + len(_CRLF))


def _take_subrecv(buf):
    """Take one `+MQTTSUBRECV` frame using its declared length.

    Returns None while the frame is still arriving — the payload can be split across
    several UART reads, and consuming a partial one would corrupt both it and whatever
    follows. A frame that is malformed rather than incomplete falls back to line
    handling, so a garbled delivery cannot wedge the buffer forever.
    """
    open_quote = buf.find(b'"', len(_SUBRECV_PREFIX))
    if open_quote == -1:
        return _take_line(buf)
    close_quote = buf.find(b'"', open_quote + 1)
    if close_quote == -1:
        return None if buf.find(_CRLF) == -1 else _take_line(buf)

    topic = buf[open_quote + 1:close_quote].decode('ascii', 'replace')

    if len(buf) <= close_quote + 1:
        return None
    if buf[close_quote + 1:close_quote + 2] != b',':
        return _take_line(buf)

    digits_start = close_quote + 2
    cursor = digits_start
    while cursor < len(buf) and 0x30 <= buf[cursor] <= 0x39:
        cursor += 1
    if cursor == digits_start:
        return _take_line(buf)
    if cursor >= len(buf):
        return None  # the length itself may still be arriving
    if buf[cursor:cursor + 1] != b',':
        return _take_line(buf)

    length = int(buf[digits_start:cursor])
    payload_start = cursor + 1
    payload_end = payload_start + length
    if len(buf) < payload_end:
        return None  # payload still in flight

    payload = buf[payload_start:payload_end].decode('ascii', 'replace')
    consumed = payload_end
    # The modem appends its own CRLF after the payload. Eat it so it is not mistaken
    # for an empty line.
    if buf[consumed:consumed + len(_CRLF)] == _CRLF:
        consumed += len(_CRLF)
    return ('message', (topic, payload), consumed)


def _take_frame(buf):
    """Take one frame from the front of `buf`.

    Returns `(kind, value, consumed)` where kind is 'line' (value is the text) or
    'message' (value is a `(topic, payload)` pair), or None when `buf` does not yet
    hold a complete frame.
    """
    if not buf:
        return None
    if buf.startswith(_SUBRECV_PREFIX):
        return _take_subrecv(buf)
    return _take_line(buf)


class MQTTATClient:
    def __init__(self, host: str, port: int = 1883, uart=None, uart_id=1, tx_pin=8, rx_pin=9, baud=115200, keepalive: int = 1, timeout_ms=2000, debug: bool = False, client_id: str = 'sensors'):
        """Create client bound to a single broker address.

        - `host` and `port` are used to connect on `setup()` and kept for diagnostics.
        - UART pins/params configure the hardware UART used to talk to the modem.
        """
        self.host = host
        self.port = port
        self.keepalive = keepalive
        # Distinct per node: two clients sharing an MQTT client id knock each other off
        # the broker in a reconnect loop, which looks exactly like flaky hardware.
        self.client_id = client_id
        self.uart_id = uart_id
        self.tx_pin = tx_pin
        self.rx_pin = rx_pin
        self.baud = baud
        self.timeout_ms = timeout_ms
        # optional externally provided UART instance
        self.uart = uart
        self._own_uart = uart is None
        self.debug = bool(debug)
        self._rx_buffer = b""
        # topic_prefix -> list(callback(topic, payload))
        self._topic_handlers = {}
        # callbacks that want all raw lines (debug)
        self._raw_handlers = []
        # last line status from modem ('OK' or 'ERROR') set by poll()
        self._last_status = None
        # callbacks for status updates: callback(status_str)
        self._status_handlers = []
        # recent raw lines received from modem (for diagnostics)
        self._recent_lines = []
        self._max_recent_lines = 200
        # Broker link state, driven by the modem's own +MQTTCONNECTED /
        # +MQTTDISCONNECTED notifications rather than inferred from command results.
        self.connected = False
        self.reconnect_backoff_ms = RECONNECT_BACKOFF_MS
        self._next_reconnect_ms = None
        self._busy = False
        self._subscriptions = []
        self._lwt = None

    def wait_for_modem(self, timeout_ms=MODEM_READY_TIMEOUT_MS,
                       probe_interval_ms=MODEM_PROBE_INTERVAL_MS):
        """Poll bare `AT` until the modem answers. True once it does, False on timeout.

        The Pico and the modem leave a power cycle together, and the Pico wins: it is
        issuing commands about 300 ms in (MicroPython's own boot, plus 19 ms of I2C and
        expander setup), while the modem needs 1.2-3.5 s to reach `ready` — the spread
        is Ethernet link negotiation. Measured on the bench, 2026-08-27.

        A command sent into that window is not refused, it is swallowed whole: no echo
        and no reply. So `AT+RST` used to time out against its 2 s default whenever the
        link took the slow path, which killed the node outright — the intermittent
        "doesn't come up after a power cycle". Ask the modem whether it is there before
        giving it an order.
        """
        deadline = _now_ms() + int(timeout_ms)
        while True:
            if self.send_at_and_wait('AT', timeout_ms=probe_interval_ms):
                # Drop whatever the modem's boot log left behind. Nothing legitimate is
                # in flight at this point, and the bytes a booting ESP emits are at
                # another baud rate entirely — noise, not frames.
                self._rx_buffer = b""
                return True
            if _now_ms() >= deadline:
                return False

    def setup(self):
        """Initialise UART, apply base config and connect to broker.

        This performs the one-time modem setup sequence: UART init,
        `set_base_config()` and `connect()` using constructor host/port.
        """
        if self.uart is None:
            # create/own the UART instance
            if UART is None or Pin is None:
                raise RuntimeError('machine.UART and machine.Pin not available; provide an existing uart instance when not on MicroPython')
            self.uart = UART(self.uart_id, baudrate=self.baud, tx=Pin(self.tx_pin), rx=Pin(self.rx_pin))

        # The modem has to be asked whether it is awake before it is given an order.
        if not self.wait_for_modem():
            self._dump('modem ready')
            raise ModemNotResponding(
                'modem did not answer AT within %d ms' % MODEM_READY_TIMEOUT_MS)

        # reset the network module in case this isn't a fresh power-on
        ok = self.send_at_and_wait('AT+RST', timeout_ms=5000)
        if not ok:
            self._log('Warning: AT+RST returned ERROR or timed out')
            self._dump('reset')
            raise ModemNotResponding('AT+RST failed')

        # wait for network ready indication from modem: '+ETH_GOT_IP:...' line
        # the module typically prints 'ready' then '+ETH_GOT_IP:<ip>' when ready.
        #
        # Check what has already arrived before waiting. On a fast link the IP can land
        # in the same read as the reset's own OK, and `wait_for_line` only inspects
        # lines recorded after it is called — so it would sit out its whole 30 s waiting
        # for a line that has been and gone, and then raise as though the network were
        # down.
        got_ip = None
        for line in self.lines_since_command():
            if line.startswith('+ETH_GOT_IP:'):
                got_ip = line
                break
        if got_ip is None:
            got_ip = self.wait_for_line('+ETH_GOT_IP:', timeout_ms=30000)
        if not got_ip:
            self._dump('post-reset')
            raise NetworkNotReady('Network device did not report IP after reset')

        config_cmd = 'AT+MQTTUSERCFG=0,1,"%s","","",0,0,""' % self.client_id
        if not self.send_at_and_wait(config_cmd, timeout_ms=50000):
            self._dump('base config')
            raise BrokerUnreachable('set_base_config returned ERROR or timed out')

        # The will, if one was registered, has to be in place before the connect —
        # the broker only reads it at connection time.
        if not self._apply_lwt():
            self._dump('lwt config')
            raise BrokerUnreachable('AT+MQTTCONNCFG returned ERROR or timed out')

        if not self._connect():
            self._dump('connect')
            raise BrokerUnreachable('connect returned ERROR or timed out')

        # `_connect()` returning True only means the modem accepted the command. The
        # broker link is confirmed by the modem's own +MQTTCONNECTED notification,
        # which `_handle_line` records — so wait for it rather than assuming.
        if not self.connected:
            if self.wait_for_line('+MQTTCONNECTED', timeout_ms=5000) is None:
                self._dump('connect')
                raise BrokerUnreachable('modem accepted AT+MQTTCONN but never reported +MQTTCONNECTED')
        self._next_reconnect_ms = None

    def _write_line(self, line: str):
        if self.uart is None:
            raise RuntimeError('Call setup() before sending commands')
        if not line.endswith('\r\n'):
            line = line + '\r\n'
        try:
            self.uart.write(line)
            if self.debug:
                self._log('TX: %s' % line.rstrip('\r\n'))
        except Exception:
            # best-effort: ignore write errors so callers can continue
            pass

    def _log(self, msg: str):
        try:
            if self.debug:
                print('[MQTTAT]', msg)
        except Exception:
            pass

    def _mark_command_boundary(self):
        """Remember where the current command starts in the diagnostics ring."""
        self._command_start_idx = len(self._recent_lines)

    def lines_since_command(self):
        """Modem output since the last command was sent.

        What a failure dump wants: the replies to *this* command, not the whole ring.
        """
        try:
            return self._recent_lines[getattr(self, '_command_start_idx', 0):]
        except Exception:
            return list(self._recent_lines)

    def _dump(self, label):
        try:
            print('--- modem response (%s) ---' % label)
            for line in self.lines_since_command():
                print(line)
            print('--- end modem response ---')
        except Exception:
            pass

    def register_status_handler(self, callback):
        """Register a callback(status_str) invoked when modem replies OK/ERROR."""
        self._status_handlers.append(callback)

    def wait_for_line(self, prefix: str, timeout_ms: 'Optional[int]' = None):
        """Block until a received raw line starts with `prefix` or timeout.

        Returns the matched line string or None on timeout. Uses `poll()` so
        it integrates with the normal line processing and appends to
        `self._recent_lines` for diagnostics.
        """
        if timeout_ms is None:
            timeout_ms = int(self.timeout_ms) * 10
        # remember start index so we only inspect new lines
        start_idx = len(self._recent_lines)
        deadline = _now_ms() + int(timeout_ms)
        while _now_ms() <= deadline:
            try:
                self.poll()
            except Exception:
                pass
            # inspect newly added lines
            try:
                for ln in self._recent_lines[start_idx:]:
                    if ln.startswith(prefix):
                        return ln
                start_idx = len(self._recent_lines)
            except Exception:
                pass
            _sleep_ms(50)
        return None

    def subscribe(self, topic: str, qos: int = 0) -> bool:
        """Subscribe, and remember it so a reconnect can restore it.

        A subscription does not survive the broker connection dropping. Without the
        bookkeeping below, a node that reconnects goes deaf: it looks healthy, keeps
        publishing, and silently stops receiving.
        """
        cmd = 'AT+MQTTSUB=0,"%s",%d' % (self._escape_at_string(topic), int(qos))
        ok = self.send_at_and_wait(cmd)
        if ok:
            if (topic, qos) not in self._subscriptions:
                self._subscriptions.append((topic, qos))
        else:
            self._log('subscribe FAILED: %s' % topic)
        return ok

    def publish(self, topic: str, message: str, qos: int = 0, retain: int = 0) -> bool:
        """Publish, returning whether the modem accepted the command.

        **The return value must be checked.** This used to be discarded, which meant a
        node whose broker had gone away carried on publishing into nothing and reporting
        success — the failure mode where the layout believes it has sensor coverage it
        does not have.

        A True here means the modem took the command, not that a subscriber received it.
        """
        esc_topic = self._escape_at_string(topic)
        esc_msg = self._escape_at_string(message)
        cmd = 'AT+MQTTPUB=0,"%s","%s",%d,%d' % (esc_topic, esc_msg, int(qos), int(retain))
        ok = self.send_at_and_wait(cmd)
        if not ok:
            self._log('publish FAILED: %s' % topic)
            # The modem refusing a publish is the first sign the link has gone, and it
            # is not always accompanied by a +MQTTDISCONNECTED. Arm the reconnect.
            if self._next_reconnect_ms is None:
                self._next_reconnect_ms = _now_ms() + self.reconnect_backoff_ms
        return ok

    def configure_lwt(self, topic: str, message: str, qos: int = 1, retain: int = 1):
        """Register a Last Will, applied on the next connect.

        Must be set before `setup()`: ESP-AT carries the will in `AT+MQTTCONNCFG`,
        which the broker only reads at connection time.

        **Nothing in this repo currently uses this.** A sensor node has no business
        publishing a will to `layout/{id}/system/status` — that topic belongs to the
        orchestrator, which registers it as its own LWT, so a node dying there would
        overwrite the orchestrator's status with "offline". Sensor liveness is the 30 s
        re-assert and the backend's freshness window instead. Kept because a future
        device-status topic would need it.
        """
        self._lwt = (topic, message, int(qos), int(retain))

    def _apply_lwt(self) -> bool:
        if self._lwt is None:
            return True
        topic, message, qos, retain = self._lwt
        cmd = 'AT+MQTTCONNCFG=0,%d,0,"%s","%s",%d,%d' % (
            self.keepalive, self._escape_at_string(topic),
            self._escape_at_string(message), qos, retain,
        )
        ok = self.send_at_and_wait(cmd)
        if not ok:
            self._log('LWT config FAILED')
        return ok

    def _maybe_reconnect(self):
        """Retry a dropped broker connection, at most once per backoff interval.

        Skipped while a command is in flight: `send_at_and_wait()` calls `poll()` in its
        wait loop, so reconnecting from there would re-enter the modem mid-command.
        """
        if self._busy or self._next_reconnect_ms is None:
            return
        if _now_ms() < self._next_reconnect_ms:
            return

        self._next_reconnect_ms = _now_ms() + self.reconnect_backoff_ms
        self._log('reconnecting to %s:%d' % (self.host, self.port))

        if not self._connect():
            return

        # Subscriptions do not survive the disconnect; restore them.
        for topic, qos in list(self._subscriptions):
            cmd = 'AT+MQTTSUB=0,"%s",%d' % (self._escape_at_string(topic), int(qos))
            if not self.send_at_and_wait(cmd):
                self._log('re-subscribe FAILED: %s' % topic)

    def _connect(self) -> bool:
        cmd = 'AT+MQTTCONN=0,"%s",%d,%d' % (
            self._escape_at_string(self.host), self.port, self.keepalive)
        return self.send_at_and_wait(cmd, timeout_ms=10000)

    def ping(self, host: str):
        cmd = f'AT+PING="{host}"'
        ok = self.send_at_and_wait(cmd)

    def register_topic_handler(self, topic_prefix: str, callback):
        """Register `callback(topic, payload)` for messages where the
        incoming topic starts with `topic_prefix`.

        `topic_prefix` may be a full topic or a prefix such as 'track/'.
        The callback receives two args: the message topic and the payload
        (both strings).
        """
        if topic_prefix not in self._topic_handlers:
            self._topic_handlers[topic_prefix] = []
        self._topic_handlers[topic_prefix].append(callback)

    def unregister_topic_handler(self, topic_prefix: str, callback):
        if topic_prefix in self._topic_handlers:
            try:
                self._topic_handlers[topic_prefix].remove(callback)
            except ValueError:
                pass

    def register_raw_handler(self, callback):
        self._raw_handlers.append(callback)

    def poll(self):
        """Read available UART bytes, split into frames, and dispatch handlers."""
        if self.uart is None:
            return
        try:
            any_bytes = getattr(self.uart, 'any', lambda: 0)()
        except Exception:
            any_bytes = 0

        if any_bytes:
            try:
                data = self.uart.read(any_bytes)
            except Exception:
                data = None
            if data:
                try:
                    self._rx_buffer += data
                except Exception:
                    pass

        # Drain whatever is complete, on every poll rather than only when new bytes
        # arrived — a frame completed by the previous read must not sit in the buffer
        # waiting for unrelated traffic to shake it loose.
        while True:
            frame = _take_frame(self._rx_buffer)
            if frame is None:
                break
            kind, value, consumed = frame
            self._rx_buffer = self._rx_buffer[consumed:]
            if kind == 'line':
                self._handle_line(value)
            else:
                self._handle_message(value[0], value[1])

        self._maybe_reconnect()

    def _record_line(self, text):
        try:
            self._recent_lines.append(text)
            if len(self._recent_lines) > self._max_recent_lines:
                self._recent_lines.pop(0)
        except Exception:
            pass

    def _handle_line(self, raw_line):
        if self.debug:
            self._log('RX: %s' % raw_line)
        self._record_line(raw_line)

        for cb in list(self._raw_handlers):
            try:
                cb(raw_line)
            except Exception:
                pass

        s = raw_line.strip()

        # Connection state. The modem announces both unprompted, so this is the only
        # honest source of truth about whether the broker is actually reachable — a
        # successful AT+MQTTPUB only means the modem accepted the command.
        if s.startswith('+MQTTCONNECTED'):
            self.connected = True
            self._next_reconnect_ms = None
            self._log('broker connected')
        elif s.startswith('+MQTTDISCONNECTED'):
            if self.connected:
                self._log('broker disconnected')
            self.connected = False
            if self._next_reconnect_ms is None:
                self._next_reconnect_ms = _now_ms() + self.reconnect_backoff_ms

        if s == 'OK' or s == 'ERROR':
            self._last_status = s
            for sh in list(self._status_handlers):
                try:
                    sh(s)
                except Exception:
                    pass

    def _handle_message(self, topic, payload):
        if self.debug:
            self._log('RX message: %s -> %s' % (topic, payload))
        # Recorded in its wire form so the diagnostics dump reads like the stream did.
        self._record_line('+MQTTSUBRECV:0,"%s",%d,%s' % (topic, len(payload), payload))

        for topic_prefix, callbacks in list(self._topic_handlers.items()):
            if topic.startswith(topic_prefix):
                for cb in list(callbacks):
                    try:
                        cb(topic, payload)
                    except Exception:
                        pass

    # Utility helper for convenience
    def publish_json(self, topic: str, obj):
        try:
            import ujson as json
        except Exception:
            try:
                import json
            except Exception:
                json = None
        if json is not None:
            try:
                payload = json.dumps(obj)
            except Exception:
                payload = str(obj)
        else:
            payload = str(obj)
        self.publish(topic, payload)

    def send_at_and_wait(self, cmd: str, timeout_ms: 'Optional[int]' = None) -> bool:
        """Send an AT command and wait for a terminal `OK` or `ERROR` response.

        Returns True on `OK`, False on `ERROR` or timeout.
        Note: this method calls `poll()` while waiting so it integrates with
        normal line processing. Avoid calling from interrupt context.
        """
        if timeout_ms is None:
            timeout_ms = int(self.timeout_ms)
        self._last_status = None
        # `_recent_lines` is deliberately NOT cleared here. It used to be, which threw
        # away the diagnostics on every command — including the lines this command's own
        # failure dump was about to print, and the ones `wait_for_line()` was scanning
        # for. It is a bounded ring; let it ring.
        self._mark_command_boundary()

        # Guards `_maybe_reconnect()`: poll() runs inside the wait loop below, and
        # reconnecting from there would re-enter the modem in the middle of this command.
        self._busy = True
        try:
            self._write_line(cmd)
            deadline = _now_ms() + int(timeout_ms)
            while _now_ms() <= deadline:
                try:
                    self.poll()
                except Exception:
                    pass

                if self._last_status is not None:
                    s = self._last_status
                    self._last_status = None
                    return s == 'OK'
                _sleep_ms(10)
        finally:
            self._busy = False

        # On timeout/failure, flush any remaining bytes in the rx buffer
        try:
            if getattr(self, '_rx_buffer', None):
                try:
                    leftover = self._rx_buffer.decode('ascii', 'replace')
                except Exception:
                    try:
                        leftover = self._rx_buffer.decode('ascii', 'ignore')
                    except Exception:
                        leftover = str(self._rx_buffer)
                if leftover:
                    try:
                        self._recent_lines.append(leftover)
                        # keep bounded
                        if len(self._recent_lines) > self._max_recent_lines:
                            self._recent_lines = self._recent_lines[-self._max_recent_lines:]
                    except Exception:
                        pass
        except Exception:
            pass
        return False

    def _escape_at_string(self, s: str) -> str:
        # escape backslash and double-quote for safe embedding in AT command quotes
        if s is None:
            return ''
        return s.replace('\\', '\\\\').replace('"', '\\"')

    def close(self):
        """Deinitialize owned UART if present."""
        try:
            if getattr(self, '_own_uart', False) and self.uart is not None:
                try:
                    self.uart.deinit()
                except Exception:
                    pass
                self.uart = None
        except Exception:
            pass

    def _parse_mqttsubrecv(self, raw_line: str):
        return self._parse_mqttsubrecv_line(raw_line)

    @staticmethod
    def _parse_mqttsubrecv_line(raw_line: str):
        """Parse one complete `+MQTTSUBRECV` frame and return (topic, payload) or None.

            +MQTTSUBRECV:0,"topic",<len>,data

        A thin wrapper over the same length-aware reader `poll()` uses, so there is one
        parser rather than two that disagree at the edges.

        **The declared length is now honoured.** The previous implementation took
        everything after the third comma up to the end of the line, which quietly
        produced a wrong answer whenever the declared length and the available bytes
        disagreed — the case a truncated or split frame always presents. A frame whose
        payload is shorter than its declared length is incomplete, not a short message,
        and returns None here.
        """
        if not isinstance(raw_line, str):
            return None
        buf = raw_line.encode('ascii', 'replace')
        if not buf.endswith(_CRLF):
            buf += _CRLF
        if not buf.startswith(_SUBRECV_PREFIX):
            return None
        frame = _take_frame(buf)
        if frame is None or frame[0] != 'message':
            return None
        return frame[1]

# expose parser alias for unit tests
try:
    parse_mqttsubrecv_line = MQTTATClient._parse_mqttsubrecv_line
except Exception:
    parse_mqttsubrecv_line = None

__all__ = ['MQTTATClient', 'ATError', 'ModemNotResponding', 'NetworkNotReady',
           'BrokerUnreachable']
