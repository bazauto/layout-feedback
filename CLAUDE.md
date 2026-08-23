# Layout Feedback — Working Agreement

MicroPython firmware for the Raspberry Pi Pico nodes that connect layout hardware — block
detectors, IO expanders, RFID readers — to MQTT, for the Westgate Hollow layout. The
orchestrator in `bazauto/layout-orchestration` consumes what these nodes publish.

**These nodes are the layout's eyes.** A node that reports wrongly, or stops reporting without
saying so, causes the orchestrator to make routing and occupancy decisions on false
information. Treat a silent failure as worse than a loud one.

## Reading this repo without burning context

This file loads into **every session and every subagent**, so every line is a tax paid on
tasks that never touch what it describes. It is an index and a rulebook, nothing else.
Reasoning belongs in `docs/`.

## Authoritative documents

| Document | What it governs |
|---|---|
| `../layout-orchestration/docs/mqtt-contract.md` | **Binding.** Topics, payloads, QoS, retention, the 30 s re-assert. This repo does not own it and must never amend it. |
| `docs/pin-allocation.md` | Which sensor is on which expander pin, and why allocation is not installation |
| `docs/DEVELOPMENT_NOTES.md` | Module conventions, the MCP23017 pin map, hardware wiring |
| `docs/PN7150_STATE_MACHINE_PLAN.md` | The NCI state machine the RFID reader implements |
| `docs/MQTT_AT_COMMANDS.md` | The ESP-AT command set the transport speaks |

## The layout

`layoutId` is **`c4e587aa-478d-46ab-a1df-9dd8359fc040`**. Topics are
`layout/{layoutId}/sensor/{sensorId}/reading`. 17 sensors are configured across 9 blocks; the
full table is in `docs/plans/2026-08-23-node-split.md`.

**Both sensor types are the same thing to this firmware.** `sensors.type`
(`block_detection` for current sensing, `ir_position` for IR) lives in the orchestrator's
database, not in the payload — both publish the identical message, and both are a digital
input line here. The difference is downstream in `domain/occupancy.ts`: an `ir_position`
reading `clear` is a **no-op**, because an unbroken beam says nothing about the rest of a
block. That matters here in exactly one way — **an IR sensor must still publish `clear`
honestly**, because the backend needs the live message to keep trusting the sensor at all.

## Non-negotiable rules

1. **The MQTT contract is binding.** Run `/mqtt-check` against any diff that touches a
   publish, a subscribe, or a payload. If the work needs a topic or field the contract does
   not define, stop — that is a change to `bazauto/layout-orchestration`, made there first.

2. **A sensor reading is re-asserted at least every 30 seconds**, unchanged, whether or not it
   changed. Publishing on change only makes the orchestrator distrust the sensor and degrade
   every block it feeds to `unknown`, which is treated as occupied. This is the rule that is
   easiest to forget and most expensive to get wrong.

3. **Never let a publish fail silently.** Check the return of every AT command. The ESP-AT
   transport has no reconnect: a discarded failure means the node loops forever believing it
   is reporting while the broker hears nothing.

4. **Hardware access stays at the edges.** Any module a test needs to reach must not import
   `machine` at module scope. Parsers, debouncers, topic builders and payload builders are
   pure and testable on the host; only the node's `main.py` and the driver classes touch
   hardware.

5. **No runtime reconfiguration.** Modules assume static wiring, configured once at cold
   start. Any structural change means a restart.

6. **Board wiring lives in one file per node** — that node's `config.py`. A module under
   `lib/` never hardcodes a pin, an address, or a `layoutId`.

7. **Publish an allow-list of installed sensors, never the whole allocation table.** An
   unwired MCP23017 input sits pulled up, reads as inactive, and would publish `clear` — a
   confident assertion that a block is empty, produced by a wire that does not exist. For a
   `block_detection` sensor that alone lets the orchestrator clear the block. See
   `docs/pin-allocation.md`.

## Hardware

| Device | Bus | Notes |
|---|---|---|
| MCP23017 IO expander | I2C0, SDA=GP4 SCL=GP5, addr `0x20`/`0x21` | 16 pins each. 0–7 = GPA0–7, 8–15 = GPB0–7. Poll-only. |
| PCF8591 ADC | I2C0, addr `0x48` | 8-bit, 4 single-ended channels. First read after a channel change is invalid. |
| PN7150 NFC | I2C1, SDA=GP2 SCL=GP3, addr `0x28` | NCI + IRQ. IRQ means "a message is ready", **not** "a tag is present". |
| TCA9548A mux | I2C1, addr `0x70` | Fans I2C1 out to up to 8 PN7150 readers |
| ESP-AT modem | UART1, TX=GP8 RX=GP9, 9600 baud | Wired ethernet; reports `+ETH_GOT_IP` when ready |

## Commands

```bash
bash scripts/run_tests.sh          # host suite (PYTHONPATH=. pytest)
python -m pytest -q                # same, directly
```

Host tests stub `machine` and `utime`; nothing in the suite touches a board. `pytest` is not
installed by default on this machine — `python -m pip install --user pytest`.

Deploying to a board is `/deploy`. The Pico is on the **bench machine**, not this one.

## Testing expectations

Every behaviour change gets a host test. The suite must stay runnable with no hardware
attached — if something genuinely cannot be tested that way, say so explicitly and name the
bench check that covers it instead. Do not leave it silently untested.

## Current state (2026-08)

**Pre-restructure.** The repo is currently a flat directory of working experiments, being
split into two deployable nodes. Until that lands, treat the file list below as the map:

- `mqtt_at.py` — ESP-AT MQTT client over UART1. The transport everything needs.
- `input_pin_monitor.py` — native Pico GPIO, IRQ + timer debounce. Not yet wired into `main.py`.
- `mcp23017_io.py`, `pcf8591_adc.py` — I2C expander and ADC drivers.
- `pn7150_mux_reader.py` — PN7150 + mux, callback-based. **The keeper.**
- `pn7150_mux_state_machine.py` — a near-duplicate of the above with its own `main()`.
- `mcu_uart_bridge.py`, `usb_uart_bridge_simple.py`, `uart_bridge_test.py` — three takes on
  one USB↔UART debug tool.
- `main.py` — flat top-level script doing IO, NFC and MQTT at once.

### Traps

- **The current topics are not contract topics.** `track/sensor/{name}` with an `ACTIVE` /
  `INACTIVE` string body is what the code publishes today; the contract wants
  `layout/{layoutId}/sensor/{sensorId}/reading` carrying JSON, retained, at QoS 1. Nothing
  this node currently publishes is consumable by the orchestrator.
- **`_parse_mqttsubrecv_line()` ignores the declared payload length** and splits on CRLF. Fine
  for `ACTIVE`/`INACTIVE`; it breaks on JSON containing `"` or `\r\n`.
- **`MCP23017Pin.update_state()` rate-limits, it does not debounce.** It compares against the
  time of the last *accepted* change, so its behaviour differs from `InputPinMonitor`'s
  candidate-and-timer debounce despite the matching method names.
- **`send_at_and_wait()` clears `_recent_lines` on every command**, which gut the diagnostics
  that `wait_for_line()` and the error dumps depend on.
- **`publish()` and `subscribe()` discard their result.** A failed publish is invisible.
- **The PN7150 IRQ is not a tag-presence pin.** It asserts when any NCI message is queued.
  Tag presence is inferred by the state machine's periodic T2T read, not by the pin.
- **Sensor names and topic ids do not agree.** Six sensors carry ids from an earlier naming
  that the topic did not follow through a rename — `CS - Siding 3` publishes to
  `main-line-east`. **Key config on the topic id and treat the name as a comment.** Wiring by
  name would put a sensor on the wrong block.
- **RFID is restructure-only** (#4). The hardware is not sensitive enough yet, and
  `bazauto/layout-orchestration#39` owns the design and amends the contract first. Do not
  invent tag topics. When it is built, a tag read is an **event, not a state** — its own
  topic, and explicitly **not retained**, the opposite of `sensor/*/reading`.
