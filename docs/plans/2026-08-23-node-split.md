# Plan — restructure, and prove two sensors end to end

## Goal

Turn a flat directory of working experiments into two deployable MicroPython nodes sharing one
library, and get **one current-sensing and one IR sensor working end to end** onto the
orchestrator — proof on every layer of the stack before any of it is mass-deployed.

Scaling to the other 15 configured sensors is deliberately *not* this work. Neither is RFID.

## Fixed inputs

From the orchestrator's Configure → Sensors (`docs/orchestration_sensors.png`, 2026-08-23):

- **`layoutId` = `c4e587aa-478d-46ab-a1df-9dd8359fc040`**
- 17 sensors configured, 9 blocks. Topics are
  `layout/c4e587aa-478d-46ab-a1df-9dd8359fc040/sensor/{sensorId}/reading`.

### D0 — Both sensor types are the same thing to this firmware

`sensors.type` is `'block_detection' | 'ir_position'` and lives **in the orchestrator's
database, not in the payload**. Both publish the identical `sensor/{sensorId}/reading` message
carrying `{ "state": "occupied" | "clear" }`. The firmware does not know or care which it is —
current sensing and IR are both a digital input line.

What differs is entirely downstream, in `domain/occupancy.ts`: an `ir_position` sensor reading
`clear` is a **no-op**, because an unbroken beam says nothing about the rest of a block. Only a
`block_detection` sensor may assert a block is empty. This matters to us in one way only:
**an IR sensor must still publish `clear` honestly.** Suppressing it as "meaningless" would be
wrong — the backend needs the live message to keep trusting the sensor at all.

### The sensor id table and pin allocation

Six ids that did not follow an earlier rename were corrected orchestrator-side on 2026-08-23,
along with a "Signing"/"Siding" typo. All 17 ids are now a clean slug of their name.

**Board 1 (`0x20`) is current sensing, board 2 (`0x21`) is IR**, allocated sequentially with
pin N meaning the same block on both boards. Board 2 pin 5 is reserved and empty because Engine
/ Goods Transfer has current sensing and no IR beam.

| Pin | Block | Board 1 `0x20` (CS) | Board 2 `0x21` (IR) |
|---|---|---|---|
| 0 | Fiddle Yard 1 | `cs---fiddle-yard-1` | `ir---fiddle-yard-1` |
| 1 | Fiddle Yard 2 | `cs---fiddle-yard-2` | `ir---fiddle-yard-2` |
| 2 | Siding 1 | `cs---siding-1` | `ir---siding-1` |
| 3 | Siding 2 | `cs---siding-2` | `ir---siding-2` |
| 4 | Siding 3 | `cs---siding-3` | `ir---siding-3` |
| 5 | Engine / Goods Transfer | `cs---engine-goods-transfer` | *reserved* |
| 6 | Engine Shed 1 | `cs---engine-shed-1` | `ir---engine-shed-1` |
| 7 | Engine Shed 2 | `cs---engine-shed-2` | `ir---engine-shed-2` |
| 8 | Goods Shed | `cs---goods-shed` | `ir---goods-shed` |

Full form, with port and chip pin numbers, in `docs/pin-allocation.md`. Pins 0–7 are GPA0–7 on
chip pins 21–28; **pin 8 is GPB0 on chip pin 1**, physically opposite the first eight.

## Design decisions

### D1 — Repo layout mirrors the device filesystem

```
src/lib/            → device /lib      (MicroPython already has /lib on sys.path)
src/apps/io-node/   → device /         (main.py + config.py)
src/apps/rfid-node/ → device /
tools/                                 host-side debug utilities, never deployed
tests/                                 host pytest, no hardware
scripts/                               run_tests.sh, deploy.sh
```

Deploying a node is copying `src/lib/*` to `/lib` and one app directory to `/`.

### D2 — A contract layer, separate from the transport

`src/lib/layout_mqtt.py` sits between a node's `main.py` and `mqtt_at.py`, owning everything
the contract specifies and the transport does not: topic building, JSON payloads, the
`system/status` LWT, the 30-second re-assert, and surfacing a failed publish instead of
swallowing it.

`mqtt_at.py` stays what it is — bytes to and from an ESP-AT modem, knowing nothing about
sensors. The split is what makes the contract rules testable: `layout_mqtt.py` imports no
`machine`, so `CLAUDE.md` rule 2 is covered by unit tests rather than by hope.

### D3 — The re-assert is a scheduler, not a sleep

`layout_mqtt.tick(now_ms)` republishes any sensor whose last publish is older than
`REASSERT_INTERVAL_MS` (default 25 000 — inside the contract's 30 s, itself well inside the
orchestrator's 90 s freshness window). A state change publishes immediately and resets that
sensor's timer.

Driven by an injected `now_ms` rather than reading the clock, so a test can advance time and
assert the republish without sleeping.

### D4 — `updatedAt` is omitted

No RTC, no NTP. The contract makes the field optional, explicitly permits a device with no
synchronised clock to omit it, and never makes a safety decision from a device-supplied
timestamp. A boot-relative value would look authoritative and be wrong.

### D5 — The whole table is allocated; only an allow-list is published

`config.py` carries all 17 sensors with their allocated pins, plus a separate `INSTALLED` list
naming the ones actually wired. Only those publish.

```python
LAYOUT_ID = "c4e587aa-478d-46ab-a1df-9dd8359fc040"

SENSORS = [
    {"sensor_id": "cs---fiddle-yard-1", "expander": 0x20, "pin": 0,
     "active_low": True, "debounce_ms": 200},
    ...
]

INSTALLED = ("<tbd>", "<tbd>")   # nothing else is published

OUTPUTS = []  # none on this layout; points are DCC. Kept for future use.
```

**This is a safety property, not tidiness.** An unwired MCP23017 input sits pulled up, which
reads as inactive, which would publish `clear` — a confident assertion that a block is empty,
produced by a wire that does not exist. For a `block_detection` sensor that is enough on its
own to clear the block (`domain/occupancy.ts` clause 2). Publishing the whole allocation table
would put fifteen of those on the broker.

Bringing a sensor online is: wire it to its allocated pin, add its id to `INSTALLED`, deploy.

An id in `INSTALLED` that is not in `SENSORS`, or a placeholder, is refused at startup rather
than published.

### D6 — Length-aware receive parsing

`+MQTTSUBRECV:0,"topic",<len>,data` carries a byte count that the current parser ignores,
splitting on CRLF instead. Fine for `ACTIVE`; broken for JSON, where a payload containing
`\r\n` arrives as two lines and one containing `"` truncates.

The receive path becomes a small state machine over the rx buffer — scan for a complete header,
then consume exactly `<len>` bytes, tolerating a payload split across several `poll()` calls.
Non-`+MQTTSUBRECV` traffic (`OK`, `ERROR`, `+ETH_GOT_IP`) stays line-oriented.

### D7 — Escaping works. Settled on the bench, 2026-08-23. ✅

**`AT+MQTTPUB` with backslash-escaped payloads is correct on this modem. `AT+MQTTPUBRAW` is
not needed.** `mqtt_at._escape_at_string()` is sufficient as written.

Run with `tools/spike_json_publish.py`. Both payloads arrived on the broker byte-identical to
what was sent, and both parse:

```
spike/layout-feedback/json {"state": "occupied"}
spike/layout-feedback/json {"note": "a \"quoted\" word and a \\ backslash"}
```

The second is the boundary case — an embedded escaped quote and a literal backslash are the
two characters that can break out of the AT argument. On the wire that command reads:

```
AT+MQTTPUB=0,"spike/...","{\"note\": \"a \\\"quoted\\\" word and a \\\\ backslash\"}",1,0
```

Modem: `OK`, and the broker received the original string. Verified by parsing what arrived,
not by trusting the modem's `OK` — the modem acknowledging a command says nothing about what
reached the broker.

### D9 — The sensors are active **high**. The old config had it backwards. ✅

Measured on the bench with a loco standing in Goods Shed and obscuring the beam, so both
sensors were known-occupied:

| Sensor | Expander | Pin | Reads | Verdict |
|---|---|---|---|---|
| `cs---goods-shed` | `0x20` | 8 | 1 | driven high |
| `ir---goods-shed` | `0x21` | 8 | 1 | driven high |

"Reads 1" alone would have been ambiguous: `create_pin` enables the MCP23017's internal
pull-up, so an *unwired* input also reads 1. Re-reading each pin with the pull-up **disabled**
separated the cases — both still read 1, so both are actively driven rather than floating.

**The pre-split `main.py` configured these as `active_low: True`,** which inverts the meaning:
it would have published `INACTIVE` for an occupied block. For a `block_detection` sensor that
is precisely the failure the fail-safe rules exist to prevent — a confident `clear` on
occupied track. `config.py` uses `active_low: False`.

This is strong evidence rather than proof; a pin hard-tied to VCC would look the same. #3's
bring-up checklist confirms it from the other end by clearing the block and watching the
reading follow.

### D8 — RFID is restructured, not implemented

The current readers cannot detect passing rolling stock reliably, and different hardware is on
order. `bazauto/layout-orchestration#39` owns the design and is explicit that the contract is
amended **first**, before any firmware.

So the RFID node is **moved and de-duplicated, and nothing else**. It keeps publishing to its
existing ad-hoc topic, marked in one clearly-commented block as pre-#39 and not contract
traffic. It does not get topic builders, retention, or a re-assert.

Two things from #39 that a future implementation must respect, recorded here so they are not
re-derived: a tag read is an **event, not a state**, so it gets its own topic and is
**explicitly not retained** — a replayed observation asserts a vehicle position that may be
days stale. And #39's sequencing names `esp-layout-controller` as the firmware home; since this
repo is now the sensor firmware, that step needs correcting on the issue.

## Plan

### PR 1 — Restructure

Mechanical, no behaviour change, so the later diffs are readable.

**Step 1 — Test scaffolding**
Files: `tests/conftest.py` (new), `tests/test_mqtt_parser.py`.
Change: fake `machine` and `utime` modules installed into `sys.modules` before any device
import, plus a `FakeUART` recording writes and replaying scripted bytes. Port the existing
parser tests onto it.
Test: the current 5 tests pass through the fake transport.

**Step 2 — Move, and delete the duplicates**
Files: everything, into the D1 layout. Delete `pn7150_mux_state_machine.py` (a near-identical
twin of `pn7150_mux_reader.py`, which keeps the callback shape the nodes need). Collapse
`mcu_uart_bridge.py`, `usb_uart_bridge_simple.py` and `uart_bridge_test.py` into one
`tools/uart_bridge.py`.
Test: suite green after path updates; `pytest.ini` gains `src` on `pythonpath`.

### PR 2 — Make the transport contract-capable

**Step 3 — Length-aware receive (D6)**
Files: `src/lib/mqtt_at.py`.
Test: payload containing `\r\n`; payload containing `"`; payload split across three `poll()`
calls; a `+MQTTSUBRECV` immediately followed by `OK` in one read.

**Step 4 — Stop losing failures**
Files: `src/lib/mqtt_at.py`.
Change: `publish()` and `subscribe()` return their result and report failure; add a `connected`
state and a reconnect path driven from `poll()`; stop `send_at_and_wait()` clearing
`_recent_lines`, which currently guts its own diagnostics.
Test: a publish answered with `ERROR` is reported, not swallowed; a dropped connection triggers
exactly one reconnect attempt per backoff interval.

**Step 5 — Escaping, with the bench spike (D7)**
Files: `src/lib/mqtt_at.py`, `tools/`.
Change: escape `\` and `"` for `AT+MQTTPUB`; **then run it on the bench and watch the payload
arrive on the broker before continuing.**
Test: host tests for escaping; the bench result quoted verbatim in the PR body.

**Step 6 — The contract layer (D2, D3, D4)**
Files: `src/lib/layout_mqtt.py` (new).
Test: topic shape; `occupied`/`clear` vocabulary; retain and QoS per the contract; **an
unchanged sensor republished after 25 s of injected time and not before**; a state change
publishing immediately and resetting the timer; a placeholder `sensor_id` refused.

### PR 3 — The IO node, and two sensors end to end

**Step 7 — `src/apps/io-node/`**
Files: `src/apps/io-node/config.py`, `main.py` (new); root `main.py` deleted.
Change: config per D5, carrying all 17 allocated sensors plus the `INSTALLED` allow-list.
`main.py` wires monitors to `layout_mqtt` and runs the loop.
Test: a sensor absent from `INSTALLED` is never published, even when its pin reads inactive —
this is the test that stops fifteen fabricated `clear`s reaching the broker; an unknown or
placeholder id in `INSTALLED` is refused at startup; a simulated pin change on an installed
sensor publishes the expected topic and payload.

**Step 8 — `scripts/deploy.sh`**
Files: `scripts/deploy.sh` (new).
Change: rsync `src/lib/` and one app to a bench staging directory, then `mpremote` by `by-id`
path to `/lib` and `/`, and soft-reset. `--dry-run` prints without writing. Matches what
`.claude/skills/deploy/SKILL.md` already documents.

**Step 9 — Bench bring-up**
The actual goal. Deploy, then verify each layer in order and quote the real output at each:

1. The node connects and the modem reports `+ETH_GOT_IP`.
2. `mosquitto_sub -t "layout/#" -v` shows both sensors' `reading` messages.
3. Payloads match the contract — JSON, `occupied`/`clear`, retained, QoS 1.
4. **Both re-assert while nothing changes.** Watch for 60 s and count.
5. The orchestrator's Configure → Sensors shows both in service and trusted, and the two
   blocks leave `unknown`.
6. Physically trigger each sensor and confirm the block occupancy follows.
7. Pull the node's power and confirm `system/status` goes `offline` via the LWT, and that the
   orchestrator degrades those blocks rather than believing the last reading.

Step 7 of that list is the one most likely to be skipped and the one that proves the safety
behaviour actually works.

### PR 4 — RFID restructure only (D8)

**Step 10 — `src/apps/rfid-node/`**
Files: `src/apps/rfid-node/config.py`, `main.py` (new).
Change: the PN7150 manager on its existing ad-hoc topic, marked pre-#39. No contract topics, no
retention, no re-assert.

**Step 11 — Docs**
Files: `CLAUDE.md`, `docs/DEVELOPMENT_NOTES.md`, `README.md`.
Change: rewrite, do not append. Every trap this work closes is **deleted** from `CLAUDE.md`'s
traps list, not left as history.

## Verification

`python -m pytest` at every step. The bench is needed at step 5 (a design input, not a
confirmation) and steps 8–9.

## Blocked: the broker does not accept LAN connections

**Nothing this repo publishes can reach the orchestrator today.** Measured 2026-08-23:

```
$ ss -lnt | grep 1883
LISTEN 0 100  127.0.0.1:1883  0.0.0.0:*
LISTEN 0 100      [::1]:1883     [::]:*
```

`/etc/mosquitto/conf.d` is empty and the main config declares no listener, so mosquitto binds
loopback only. The modem gets a LAN address (`172.18.10.98`) and is refused — the spike's first
run failed with `+MQTTDISCONNECTED:0` immediately after `AT+MQTTCONN`.

`layout-orchestration`'s deploy skill already records this and names the reason it has not been
fixed: opening a LAN listener carries an authentication question, and mosquitto 2.x will not
allow anonymous access on a non-loopback listener without being told to.

This blocks step 9 entirely and is tracked as #7. The spike was completed against a throwaway
broker on port 1884, started and killed inside the same session, which touched neither the
production broker's config nor its persistence store.

## Risks

- **The modem may not accept escaped JSON** (D7). Mitigated by spiking it at step 5.
- **The 25 s re-assert adds steady broker traffic** — one retained publish per sensor per 25 s.
  Nothing at two sensors; worth a look at the broker before scaling to 17.
- **A wrong `sensor_id` publishes into the void, silently.** The orchestrator has no way to
  tell us we picked a topic nobody subscribes to. Step 9's list is what catches it, which is
  why it checks the orchestrator UI and not just the broker.
- **Step 2 moves every file at once**, putting a rename commit in the history. Better than
  dribbling moves through later steps.

## Out of scope

- Amending `docs/mqtt-contract.md` — that happens in `bazauto/layout-orchestration`.
- The other 15 sensors. Prove two first.
- RFID beyond the restructure (D8), pending hardware and #39.
- `pcf8591_adc.py` beyond moving it. It is wired to nothing; analog readings have no contract
  topic and that is its own piece of work.
- `input_pin_monitor.py` gaining a node. It is the better debouncer and is currently unused;
  wiring native GPIO sensors is a follow-up.
- The MCP23017 debounce semantics (a known trap) — its own change, with its own tests, not
  smuggled into a restructure.
