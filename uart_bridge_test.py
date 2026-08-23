"""Minimal test harness for the UART bridge.

Run this on the Pico (as `uart_bridge_test.py`) to isolate the bridge behaviour
without other monitors running. It prints a heartbeat so you can confirm the
main loop stays active and logs forwarded bytes in debug mode.
"""

from mcu_uart_bridge import UARTBridge
from utime import sleep, ticks_ms

# Set to True to run blocking interactive mode that forwards REPL input to UART1.
# You can also call `b.interactive_mode()` from the REPL after importing this module.
INTERACTIVE = True


def main():
    b = UARTBridge(uart_id=1, tx_pin=8, rx_pin=9, baud=9600)
    b.debug = True
    b.setup()
    print("UART bridge config:", b.config_summary())
    print("Interactive mode available: set INTERACTIVE=True or call b.interactive_mode() from REPL")

    if INTERACTIVE:
        b.interactive_mode()
        b.close()
        print("UART bridge interactive session ended")
        return

    last_hb = ticks_ms()
    try:
        while True:
            forwarded = b.poll()
            if forwarded:
                print("forwarded bytes:", forwarded)

            # heartbeat every 5s
            if ticks_ms() - last_hb > 5000:
                print("heartbeat")
                last_hb = ticks_ms()

            sleep(0.05)
    except KeyboardInterrupt:
        print("Test interrupted by user")
    finally:
        b.close()
        print("UART bridge test finished")


if __name__ == '__main__':
    main()
