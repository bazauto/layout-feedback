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


class MQTTATClient:
    def __init__(self, host: str, port: int = 1883, uart=None, uart_id=1, tx_pin=8, rx_pin=9, baud=115200, keepalive: int = 1, timeout_ms=2000, debug: bool = False):
        """Create client bound to a single broker address.

        - `host` and `port` are used to connect on `setup()` and kept for diagnostics.
        - UART pins/params configure the hardware UART used to talk to the modem.
        """
        self.host = host
        self.port = port
        self.keepalive = keepalive
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

        # reset the network module in case this isn't a fresh power-on
        ok = self.send_at_and_wait('AT+RST')
        if not ok:
            self._log('Warning: AT+RST returned ERROR or timed out')
            raise RuntimeError('AT+RST failed')

        # wait for network ready indication from modem: '+ETH_GOT_IP:...' line
        # the module typically prints 'ready' then '+ETH_GOT_IP:<ip>' when ready.
        got_ip = self.wait_for_line('+ETH_GOT_IP:', timeout_ms=30000)
        if not got_ip:
            try:
                print('--- modem response (post-reset) ---')
                for ln in self._recent_lines:
                    print(ln)
                print('--- end modem response ---')
            except Exception:
                pass
            raise RuntimeError('Network device did not report IP after reset')

        # send base config then connect; wait for OK/ERROR responses
        config_cmd = 'AT+MQTTUSERCFG=0,1,"sensors","","",0,0,""'
        try:
            ok = self.send_at_and_wait(config_cmd, timeout_ms=50000)
            if not ok:
                # dump recent modem lines for diagnostics
                try:
                    print('--- modem response (base config) ---')
                    for ln in self._recent_lines:
                        print(ln)
                    print('--- end modem response ---')
                except Exception:
                    pass
                raise RuntimeError('set_base_config returned ERROR or timed out')
        except Exception as e:
            self._log('set_base_config failed: %s' % e)
            raise

        conn_cmd = f'AT+MQTTCONN=0,"{self.host}",{self.port},{self.keepalive}'
        try:
            ok = self.send_at_and_wait(conn_cmd, timeout_ms=5000)
            if not ok:
                # dump recent modem lines for diagnostics
                try:
                    print('--- modem response (connect) ---')
                    for ln in self._recent_lines:
                        print(ln)
                    print('--- end modem response ---')
                except Exception:
                    pass
                raise RuntimeError('connect returned ERROR or timed out')
        except Exception as e:
            self._log('connect failed: %s' % e)
            raise

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

    def subscribe(self, topic: str, qos: int = 0):
        cmd = f'AT+MQTTSUB=0,"{topic}",{qos}'
        ok = self.send_at_and_wait(cmd)

    def publish(self, topic: str, message: str, qos: int = 0, retain: int = 0):
        esc_topic = self._escape_at_string(topic)
        esc_msg = self._escape_at_string(message)
        cmd = 'AT+MQTTPUB=0,"%s","%s",%d,%d' % (esc_topic, esc_msg, int(qos), int(retain))
        ok = self.send_at_and_wait(cmd)

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
        """Read available UART bytes, split into lines, and dispatch handlers."""
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
            if not data:
                return
            # append bytes
            try:
                self._rx_buffer += data
            except Exception:
                return
                # process CRLF-terminated ASCII lines (firmware uses ASCII + CRLF)
            while True:
                sep = b'\r\n'
                idx = self._rx_buffer.find(sep)
                if idx == -1:
                    break
                raw_bytes = self._rx_buffer[:idx]
                # remove processed bytes including CRLF
                self._rx_buffer = self._rx_buffer[idx+2:]
                try:
                    raw_line = raw_bytes.decode('ascii', 'replace')
                except Exception:
                    raw_line = raw_bytes.decode('ascii', 'ignore')

                if self.debug:
                    self._log('RX: %s' % raw_line)

                # record for diagnostics (bounded) for every received line
                try:
                    self._recent_lines.append(raw_line)
                    if len(self._recent_lines) > self._max_recent_lines:
                        # drop oldest
                        self._recent_lines.pop(0)
                except Exception:
                    pass
                for cb in list(self._raw_handlers):
                    try:
                        cb(raw_line)
                    except Exception:
                        pass

                # check for OK/ERROR status lines
                s = raw_line.strip()
                if s == 'OK' or s == 'ERROR':
                    self._last_status = s
                    for sh in list(self._status_handlers):
                        try:
                            sh(s)
                        except Exception:
                            pass
                    # do not treat OK/ERROR as mqtt message lines
                    continue

                # parse +MQTTSUBRECV lines into (topic, payload) and dispatch
                parsed = self._parse_mqttsubrecv(raw_line)
                if parsed is not None:
                    topic, payload = parsed
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
        # reset previous status
        self._last_status = None
        # reset previous status and recent lines
        self._recent_lines = []
        self._write_line(cmd)
        deadline = _now_ms() + int(timeout_ms)
        while _now_ms() <= deadline:
            # process any incoming bytes/lines
            try:
                self.poll()
            except Exception:
                pass

            if self._last_status is not None:
                s = self._last_status
                self._last_status = None
                return s == 'OK'
            _sleep_ms(10)

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
        """Parse a `+MQTTSUBRECV` ASCII line and return (topic, payload) or None.

        Expected format:
            +MQTTSUBRECV:0,"topic",<len>,data

        This performs simple string parsing and assumes the modem always sends
        full ASCII lines terminated by CRLF.
        """
        prefix = '+MQTTSUBRECV:'
        if not isinstance(raw_line, str):
            return None
        if not raw_line.startswith(prefix):
            return None
        try:
            rest = raw_line[len(prefix):]
            q1 = rest.find('"')
            if q1 == -1:
                return None
            q2 = rest.find('"', q1+1)
            if q2 == -1:
                return None
            topic = rest[q1+1:q2]
            after = rest[q2+1:]
            if after.startswith(','):
                after = after[1:]
            c = after.find(',')
            if c == -1:
                payload = ''
            else:
                payload = after[c+1:]
                if payload.startswith('"') and payload.endswith('"') and len(payload) >= 2:
                    payload = payload[1:-1]
            return topic, payload
        except Exception:
            return None

# expose parser alias for unit tests
try:
    parse_mqttsubrecv_line = MQTTATClient._parse_mqttsubrecv_line
except Exception:
    parse_mqttsubrecv_line = None

__all__ = ['MQTTATClient']
