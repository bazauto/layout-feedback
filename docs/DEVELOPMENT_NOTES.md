# Pico Layout Feedback – Development Notes

This document captures the conventions and architectural decisions we have made so far so the next sensor/actuator modules and the eventual Ethernet/MQTT path stay consistent.

## Digital Input Monitoring

- Use `InputPinMonitor` for all digital inputs. It encapsulates pin init, interrupt-driven detection, timer-based debouncing, and an optional polled fallback.
- Constructor signature: `InputPinMonitor(pin_id, pull=Pin.PULL_UP, debounce_ms=25, trigger=Pin.IRQ_RISING | Pin.IRQ_FALLING, timer_id=None, use_interrupts=True)`.
- Always call `setup()` during system bootstrap; configuration is static and only occurs on cold start.
- Prefer interrupts (`use_interrupts=True`) for real-time responsiveness. When hardware IRQs are scarce or a pin should be sampled manually, set `use_interrupts=False` and call `update_state()` from the polling loop.
- Debouncing happens via a one-shot `Timer` per monitor. Choose `debounce_ms` per sensor; `0` disables debouncing and reports changes immediately without queueing duplicate events.
- `update_state()` must be polled regularly even for interrupt-backed monitors; it drains queued changes and lets the application react (e.g., toggling LEDs, enqueueing MQTT messages).

### MCP23017 IO Expander

- Use `MCP23017Expander` (see `mcp23017_io.py`) to manage the device attached to I2C0 (SDA=GP4, SCL=GP5, address `0x20` by default).
- Create pins via `expander.create_pin(pin_number, mode="input"|"output", pull_up=True, debounce_ms=25, initial_state=0)`.
- Input pins expose `setup()`, `update_state()`, and `state` just like `InputPinMonitor`. They are poll-only, so schedule `update_state()` calls in the main loop.
- Output pins use `setup()`, `write(level)`, and `toggle()`; state is cached to avoid unnecessary reads.
- Because the expander writes whole bytes, all `MCP23017Pin` instances should be created from the same `MCP23017Expander` so register caching stays consistent across pins.
- Pin numbering is zero-based: 0-7 map to GPA0-GPA7, 8-15 map to GPB0-GPB7. Keep a quick reference handy when wiring.

| Pin Number | MCP23017 Port | Physical Pin (28-pin PDIP) |
|------------|---------------|----------------------------|
| 0          | GPA0          | 21                         |
| 1          | GPA1          | 22                         |
| 2          | GPA2          | 23                         |
| 3          | GPA3          | 24                         |
| 4          | GPA4          | 25                         |
| 5          | GPA5          | 26                         |
| 6          | GPA6          | 27                         |
| 7          | GPA7          | 28                         |
| 8          | GPB0          | 1                          |
| 9          | GPB1          | 2                          |
| 10         | GPB2          | 3                          |
| 11         | GPB3          | 4                          |
| 12         | GPB4          | 5                          |
| 13         | GPB5          | 6                          |
| 14         | GPB6          | 7                          |
| 15         | GPB7          | 8                          |

### UART Bridge (USB <-> UART1)

- `mcu_uart_bridge.py` provides a small poll-driven bridge.
- By default it forwards external UART1 -> USB console (UART1 pins: TX=GPIO8, RX=GPIO9).
- It attempts to enable USB->UART forwarding (typing in the USB console is forwarded to UART1) using `uselect.poll()` on `sys.stdin`. Some MicroPython builds may not support polling stdin — the bridge falls back to uni-directional forwarding in that case.
- To use: instantiate `UARTBridge`, call `setup()`, and call `poll()` regularly from the main loop. The bridge uses non-blocking polls and reads up to 64 bytes when data is available.

Simple alternative:

- `usb_uart_bridge_simple.py` provides a minimal line-oriented bridge that forwards USB console lines to UART1 (and polls UART1 for incoming bytes to print to USB). It defaults to `9600` baud to match your hardware defaults.
- This script is intentionally blocking when in `INTERACTIVE` mode (uses `input()`), which is robust across MicroPython builds that don't expose selectable stdin. Use the `INTERACTIVE` flag at the top of the file to choose blocking interactive mode or poll-only mode.

## Future Module Patterns

To keep parity across upcoming monitor types (analog inputs, digital outputs, etc.):

1. **Consistent API** – expose `setup()` and `update_state()` (or equivalent) so application code can manage mixed sensor lists uniformly.
2. **Config Objects** – keep constructor arguments descriptive and keyword-only when possible; mirror naming used in `InputPinMonitor` (`pin_id`, `pull`, `debounce_ms`).
3. **State Accessors** – provide a lightweight `state` property returning the latest stable value without touching hardware.
4. **No Runtime Reconfiguration** – modules assume static wiring; any structural change requires a microcontroller restart.
5. **Main function** – keep the main function lean. It will contain only configuration for the various modules and the linkage between modules and the MQTT.

## Ethernet / MQTT (Future)

- Once Ethernet hardware is available, add a publisher module responsible for translating monitor events into MQTT topics.
- Keep transport code decoupled from input modules: monitors emit events, the MQTT layer subscribes to those events.
- Plan for batching or rate limiting so noisy inputs cannot overwhelm the network stack.

## Testing & Diagnostics

- Continue using `main.py` (or successors) as a simple integration test: instantiate monitors, call `update_state()` in a loop, and print transitions.
- For debugging interrupts, consider temporarily setting `debounce_ms=0` and `use_interrupts=True` to verify hardware edges, then re-enable debouncing.

## File Organization

- Keep reusable modules (input monitors, future analog/digital handlers) at the project root or a shared package directory once multiple files exist.
- Documentation like this file should live alongside the code (`DEVELOPMENT_NOTES.md`) so future contributors can quickly align with existing conventions.
