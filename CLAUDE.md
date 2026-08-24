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
| `docs/block-detector-wiring.md` | The LM-iD output stage: its two modes, why Input A is unpowered, the 3.3 V hazard if it is not |
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

3. **Never let a publish fail silently.** `publish()` and `subscribe()` return whether the
   modem accepted the command, and that return must be checked. A discarded failure means the
   node loops forever believing it is reporting while the broker hears nothing — worse than a
   crash, which at least shows up. A True means the modem took the command, not that a
   subscriber received it.

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
python -m pytest                   # host suite; paths come from pytest.ini
bash scripts/run_tests.sh          # same, passes arguments through
bash scripts/deploy.sh io-node     # deploy; refuses on a red suite
```

Host tests stub `machine` and `utime`; nothing in the suite touches a board.

Deploying is `/deploy`. The Pico is on the **bench machine**, not this one.

## Testing expectations

Every behaviour change gets a host test. The suite must stay runnable with no hardware
attached — if something genuinely cannot be tested that way, say so explicitly and name the
bench check that covers it instead. Do not leave it silently untested.

## Current state (2026-08)

The IO node is **deployed and working end to end** (#1, #2, #3). Both Goods Shed sensors
publish contract readings that the orchestrator trusts, and the block follows them.

```
src/lib/     -> device /lib
  mqtt_at.py            ESP-AT transport. Length-aware frames, reconnect, failures returned.
  layout_mqtt.py        The contract: topics, payloads, QoS, retention, the 25 s re-assert.
  sensor_wiring.py      Allocation vs installed, and the occupancy polarity. No `machine`.
  mcp23017_io.py        I2C expander, in and out. Poll-only.
  input_pin_monitor.py  Native Pico GPIO, IRQ + timer debounce. Currently unused.
  pcf8591_adc.py        I2C ADC. Wired to nothing; no contract topic for analog yet.
  pn7150.py             PN7150 + TCA9548A mux, callback-based. Awaiting #4.
src/apps/io-node/       -> device /   main.py + config.py. Deployed.
tools/       host-side, never deployed
  uart_bridge.py        Talk to the ESP-AT modem by hand.
  mqtt_example.py       Minimal publish/subscribe against a real modem.
  spike_json_publish.py The D7 bench spike, kept because it reproduces the evidence.
tests/       host pytest; conftest.py stubs `machine` and `utime`
```

Because `src/lib/*` is deployed to `/lib`, which MicroPython puts on `sys.path`, a node
imports `mqtt_at` and not `lib.mqtt_at`. `pytest.ini` puts `src/lib` and the node's own
directory on the path for the same reason.

**`src/apps/rfid-node/` does not exist yet** — #4, and it is restructure-only.

### Hardware facts established on the bench

- The board is a **Pico 2 (RP2350), MicroPython 1.27.0**, on the bench at
  `by-id` `0d9a62134acbf42d`. Two other USB serial devices on that box belong to other
  projects; one is PicoDCC's debug probe.
- **`sys.path` is `['', '.frozen', '/lib']`** — the filesystem root shadows `/lib`. A stale
  module at the root silently wins over a freshly deployed one.
- **Any `mpremote` command stops the running node**, dropping it to the raw REPL until the
  next reset.
- `AT+MQTTPUB` honours backslash escaping, so JSON needs no `AT+MQTTPUBRAW`.
- The block detectors are **active low** — occupied reads 0. `ACTIVE_LOW = True`, and it is
  correct however Input A is wired. An earlier "active high" note here was #8's, superseded
  by #11; see `docs/block-detector-wiring.md` for why both sessions saw what they saw.

### Traps

- **A broken sensor wire reads as `clear`, not `occupied`** (#9). The sensors switch to
  ground, so an open circuit floats up to the pull-up — the permissive state. The re-assert
  cannot catch it: the node is alive and republishing. Accepted knowingly — but #9 rejected
  the closed-circuit fix on a premise since shown false, so it is reopened
  (`docs/block-detector-wiring.md`).
- **The block detectors' Input A is deliberately unpowered, and that is load-bearing.**
  Powering it — which every manufacturer datasheet tells you to do — makes the output idle at
  **5 V** into 3.3 V expander inputs. Do not wire it without reading
  `docs/block-detector-wiring.md`.
- **You cannot determine this sensor's polarity by reading an expander pin.** A floating
  MCP23017 input and a driven-high one both read 1. Meter `B` against 0 V, off the expander.
  Getting this wrong has cost two bench sessions and one wrong conclusion in the repo.
- **Pin numbers here are logical and 0-based**, not header positions. Logical 8 is GPB0 on
  chip pin 1, physically opposite pins 0–7, and header labels are 1-based. Counting has got
  this wrong twice. Scan all 32 pins and see which one moves instead.
- **The PN7150 IRQ is not a tag-presence pin.** It asserts when any NCI message is queued.
  Tag presence is inferred by the state machine's periodic T2T read, not by the pin.
- **Key config on the topic id, not the sensor's display name.** They agreed as of
  2026-08-23, but they are independent fields in the orchestrator and a rename does not
  update the topic. Six had already drifted once.
- **RFID is restructure-only** (#4). The hardware is not sensitive enough yet, and
  `bazauto/layout-orchestration#39` owns the design and amends the contract first. Do not
  invent tag topics. When it is built, a tag read is an **event, not a state** — its own
  topic, and explicitly **not retained**, the opposite of `sensor/*/reading`.
