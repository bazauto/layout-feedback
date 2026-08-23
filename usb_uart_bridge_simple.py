"""Simple USB <-> UART1 bridge (line-oriented USB->UART).

- Default baud 9600 (adjustable).
- Mode: line-based interactive forwarding from USB console to UART1.
- Always polls UART1 and prints incoming bytes to USB console.

Usage: copy this to the Pico and run as `main.py` or run from REPL.
"""

from machine import UART, Pin
from utime import sleep, ticks_ms
import sys


BAUD = 9600
UART_ID = 1
TX_PIN = 8
RX_PIN = 9

# Set to True to use blocking interactive mode (uses input()).
INTERACTIVE = True

# How long (ms) to poll UART for responses after sending a line
RESPONSE_WINDOW_MS = 300


def setup_uart(baud=BAUD):
    u = UART(UART_ID, baudrate=baud, tx=Pin(TX_PIN), rx=Pin(RX_PIN))
    return u


def forward_uart_to_usb(u):
    data = None
    try:
        data = u.read()
    except Exception:
        data = None

    if data:
        # attempt to decode, fall back to repr
        # try:
        #     s = data.decode('utf-8', 'replace')
        # except Exception:
        #     s = repr(data)
        s = data
        try:
            sys.stdout.write(s)
            flush = getattr(sys.stdout, 'flush', None)
            if callable(flush):
                flush()
        except Exception:
            pass
        return len(data)
    return 0


def interactive_bridge():
    u = setup_uart()
    print('USB<->UART1 bridge running: baud', BAUD)
    print('Type lines in the USB console; each line is sent with CRLF to UART1')
    try:
        while True:
            try:
                line = input('> ')
            except KeyboardInterrupt:
                raise
            except Exception:
                # input may raise if console not available
                line = None

            if line is not None:
                try:
                    b = str(line) + '\r\n'
                    n = u.write(b)
                    print(f'[sent] {n} bytes')
                except Exception as e:
                    print('write failed:', e)

                # poll for a short response window
                start = ticks_ms()
                while ticks_ms() - start < RESPONSE_WINDOW_MS:
                    forwarded = forward_uart_to_usb(u)
                    sleep(0.02)
            else:
                # no input available; still forward any UART data
                forwarded = forward_uart_to_usb(u)
                sleep(0.05)
    except KeyboardInterrupt:
        print('Interactive bridge stopped')
    finally:
        try:
            u.deinit()
        except Exception:
            pass


def poll_only_bridge():
    u = setup_uart()
    print('UART1->USB poll bridge running: baud', BAUD)
    try:
        while True:
            forwarded = forward_uart_to_usb(u)
            if not forwarded:
                sleep(0.05)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            u.deinit()
        except Exception:
            pass


if __name__ == '__main__':
    if INTERACTIVE:
        interactive_bridge()
    else:
        poll_only_bridge()
