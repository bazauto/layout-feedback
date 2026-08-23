"""UART1 bridge: forward data received on UART1 (GPIO8/9) to the USB REPL.

A bench tool for talking to the ESP-AT modem by hand — typing AT commands and
watching the replies. It is **not deployed to a node**; copy it to a board only when
you need to drive the modem directly.

Usage:
    from uart_bridge import UARTBridge

    bridge = UARTBridge(uart_id=1, tx_pin=8, rx_pin=9, baud=9600)
    bridge.setup()

    # in your main loop:
    bridge.poll()

Or run it as a script for a blocking interactive session — see `main()` at the bottom.

This module intentionally forwards incoming bytes to `sys.stdout.write()`
instead of touching UART(0) to avoid stomping the USB REPL configuration.

Consolidated from `mcu_uart_bridge.py`, `usb_uart_bridge_simple.py` and
`uart_bridge_test.py`, which were three takes on this one tool (#1). The class below
is unchanged from `mcu_uart_bridge.py` — a restructure is not the place to rewrite a
tool that can only be tested against real hardware. The other two are gone: the
"simple" variant was a line-oriented reimplementation of `interactive_mode()`, and the
"test" harness is now `main()`.
"""

from machine import UART, Pin
import sys
try:
    import uselect
except Exception:
    uselect = None


class UARTBridge:
    """Poll-driven bridge from a hardware UART -> USB/REPL stdout.

    By default it uses UART1 on pins 8 (TX) and 9 (RX) at 115200 baud.
    Call `setup()` once, then call `poll()` frequently from your main loop.
    """

    def __init__(self, uart_id=1, tx_pin=8, rx_pin=9, baud=115200):
        self.uart_id = uart_id
        self.tx_pin = tx_pin
        self.rx_pin = rx_pin
        self.baud = baud
        self._uart = None
        self.debug = False
        # UART format defaults
        self.bits = 8
        self.parity = None
        self.stop = 1

    def setup(self):
        """Initialise the hardware UART.

        Note: This only configures the peripheral used for external pins.
        We avoid reconfiguring UART(0) to keep the USB REPL stable.
        """
        # create UART with explicit format to avoid any port defaults ambiguity
        self._uart = UART(self.uart_id, baudrate=self.baud,
                          bits=self.bits, parity=self.parity, stop=self.stop,
                          tx=Pin(self.tx_pin), rx=Pin(self.rx_pin))
        # Try to enable non-blocking USB->UART forwarding if the port supports it
        self._usb_forward_supported = False
        self._usb_poller = None
        if uselect is not None:
            try:
                p = uselect.poll()
                p.register(sys.stdin, uselect.POLLIN)
                p.poll(0)
                self._usb_poller = p
                self._usb_forward_supported = True
            except Exception:
                self._usb_poller = None
                self._usb_forward_supported = False
        # quick stdout/UART0 sanity check when debug enabled
        if getattr(self, 'debug', False):
            try:
                sys.stdout.write("[setup] stdout OK\n")
                try:
                    sys.stdout.flush()
                except Exception:
                    pass
            except Exception:
                pass

            # Try a guarded write to UART(0) and report result to stdout
            try:
                u0 = UART(0)
                try:
                    res = u0.write(b"[setup] uart0 write test\n")
                    sys.stdout.write(f"[setup] UART0.write returned: {res}\n")
                    try:
                        sys.stdout.flush()
                    except Exception:
                        pass
                except Exception as e:
                    sys.stdout.write(f"[setup] UART0.write failed: {e}\n")
                    try:
                        sys.stdout.flush()
                    except Exception:
                        pass
            except Exception:
                # If constructing UART(0) is not permitted, ignore gracefully
                try:
                    sys.stdout.write("[setup] UART0 not available\n")
                    try:
                        sys.stdout.flush()
                    except Exception:
                        pass
                except Exception:
                    pass

    def config_summary(self):
        """Return current UART bridge configuration as a dict."""
        return {
            "uart_id": self.uart_id,
            "tx_pin": self.tx_pin,
            "rx_pin": self.rx_pin,
            "baud": self.baud,
            "bits": self.bits,
            "parity": self.parity,
            "stop": self.stop,
            "usb_forward": self._usb_forward_supported,
        }

    def poll(self):
        """Poll UART1 and (optionally) USB stdin; forward data both ways.

        Returns the number of bytes forwarded this call.
        """
        if self._uart is None:
            raise RuntimeError("Call setup() before poll().")

        forwarded = 0

        # UART -> USB
        try:
            data = self._uart.read()
        except Exception:
            data = None

        if data:
            try:
                decoded = data.decode("utf-8", "replace")
            except Exception:
                decoded = None

            try:
                if self.debug:
                    hex_repr = data.hex() if isinstance(data, (bytes, bytearray)) else repr(data)
                    sys.stdout.write(f"[UART->USB] hex={hex_repr} decoded={repr(decoded)}\n")
                else:
                    if decoded is not None:
                        sys.stdout.write(decoded)
                    else:
                        sys.stdout.write(repr(data))
                # flush if available so consoles update immediately
                try:
                    flush = getattr(sys.stdout, "flush", None)
                    if callable(flush):
                        flush()
                except Exception:
                    pass
            except Exception:
                pass

            forwarded += len(data) if isinstance(data, (bytes, bytearray)) else 0

        # USB -> UART (non-blocking if supported)
        if self._usb_forward_supported and self._usb_poller is not None:
            try:
                events = self._usb_poller.poll(0)
                if events:
                    try:
                        if self.debug:
                            sys.stdout.write(f"[USB->UART] poll events={events}\n")
                            try:
                                sys.stdout.flush()
                            except Exception:
                                pass
                        # attempt to read bytes one-by-one to avoid sys.stdin.read() returning None
                        parts = []
                        while True:
                            try:
                                c = sys.stdin.read(1)
                            except Exception as e:
                                if self.debug:
                                    sys.stdout.write(f"[USB->UART] stdin.read(1) raised: {e}\n")
                                    try:
                                        sys.stdout.flush()
                                    except Exception:
                                        pass
                                c = None
                            if not c:
                                break
                            parts.append(c)
                            # small safety: don't loop forever
                            if len(parts) >= 256:
                                break
                    except Exception as e:
                        parts = []
                        if self.debug:
                            sys.stdout.write(f"[USB->UART] poll handling raised: {e}\n")
                            try:
                                sys.stdout.flush()
                            except Exception:
                                pass
                    if not parts:
                        if self.debug:
                            sys.stdout.write("[USB->UART] no stdin data returned (after single-byte reads)\n")
                            try:
                                sys.stdout.flush()
                            except Exception:
                                pass
                    else:
                        incoming = ''.join(parts)
                        try:
                            if self.debug:
                                sys.stdout.write(f"[USB->UART] repr={repr(incoming)}\n")
                                try:
                                    sys.stdout.flush()
                                except Exception:
                                    pass
                            b = incoming.encode("utf-8")
                            try:
                                written = self._uart.write(b)
                                if self.debug:
                                    sys.stdout.write(f"[USB->UART] wrote {written} bytes to UART\n")
                                    try:
                                        sys.stdout.flush()
                                    except Exception:
                                        pass
                                forwarded += len(b)
                            except Exception as e:
                                if self.debug:
                                    sys.stdout.write(f"[USB->UART] UART.write() raised: {e}\n")
                                    try:
                                        sys.stdout.flush()
                                    except Exception:
                                        pass
                        except Exception as e:
                            if self.debug:
                                sys.stdout.write(f"[USB->UART] error handling incoming: {e}\n")
                                try:
                                    sys.stdout.flush()
                                except Exception:
                                    pass
            except Exception:
                self._usb_forward_supported = False

        return forwarded

    def close(self):
        """Deinitialize the UART peripheral if supported."""
        if self._uart is not None:
            try:
                self._uart.deinit()
            except Exception:
                pass
            self._uart = None

    def interactive_mode(self, prompt='> '):
        """Blocking interactive mode: read lines from USB console and send to UART1.

        Useful for manual testing: run this from the REPL or call it from your
        main script to type commands that are forwarded to the external UART.
        This intentionally blocks until interrupted (Ctrl+C).
        """
        if self._uart is None:
            raise RuntimeError('Call setup() before interactive_mode().')
        try:
            while True:
                try:
                    line = input(prompt)
                except EOFError:
                    break
                except KeyboardInterrupt:
                    raise

                if not isinstance(line, str):
                    continue
                # send line plus CRLF to match AT command expectations
                try:
                    b = line.encode('utf-8') + b'\r\n'
                    written = self._uart.write(b)
                    if self.debug:
                        try:
                            sys.stdout.write(f"[interactive] wrote={written} bytes\n")
                            sys.stdout.flush()
                        except Exception:
                            pass
                    # if hardware loopback is available, try to read immediate response
                    try:
                        any_bytes = getattr(self._uart, 'any', lambda: 0)()
                        if any_bytes:
                            resp = self._uart.read(any_bytes)
                            if self.debug:
                                try:
                                    sys.stdout.write(f"[interactive] loopback read={resp!r}\n")
                                    sys.stdout.flush()
                                except Exception:
                                    pass
                    except Exception:
                        pass
                except Exception as e:
                    if self.debug:
                        try:
                            sys.stdout.write(f"[interactive] UART.write() raised: {e}\n")
                            sys.stdout.flush()
                        except Exception:
                            pass
        except KeyboardInterrupt:
            # user cancelled interactive mode
            return


# --- Running this file directly -------------------------------------------
#
# Was `uart_bridge_test.py`. Isolates the bridge from everything else so you can
# confirm the loop stays alive and see forwarded bytes, without a node's monitors
# running alongside.

# Blocking interactive session (type AT commands) vs. poll-only (watch the UART).
INTERACTIVE = True

BAUD = 9600
UART_ID = 1
TX_PIN = 8
RX_PIN = 9


def main():
    from utime import sleep, ticks_ms

    bridge = UARTBridge(uart_id=UART_ID, tx_pin=TX_PIN, rx_pin=RX_PIN, baud=BAUD)
    bridge.debug = True
    bridge.setup()
    print("UART bridge config:", bridge.config_summary())

    if INTERACTIVE:
        bridge.interactive_mode()
        bridge.close()
        print("UART bridge interactive session ended")
        return

    last_heartbeat = ticks_ms()
    try:
        while True:
            forwarded = bridge.poll()
            if forwarded:
                print("forwarded bytes:", forwarded)

            if ticks_ms() - last_heartbeat > 5000:
                print("heartbeat")
                last_heartbeat = ticks_ms()

            sleep(0.05)
    except KeyboardInterrupt:
        print("Interrupted by user")
    finally:
        bridge.close()
        print("UART bridge finished")


if __name__ == "__main__":
    main()
