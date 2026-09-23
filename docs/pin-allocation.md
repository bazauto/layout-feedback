# Pin allocation — IO node

Two MCP23017 expanders on I2C0 (SDA=GP4, SCL=GP5). **Board 1 (`0x20`) is current sensing,
board 2 (`0x21`) is IR.** Both sensor types are a digital input line; the distinction exists
in the orchestrator's database, not in the wiring or the payload.

Source of truth for `sensorId` is the orchestrator's Configure → Sensors
(`docs/orchestration_sensors.png`, `layoutId` `c4e587aa-478d-46ab-a1df-9dd8359fc040`).

## The alignment rule

**Pin N is the same block on both boards.** Board 2's pin 5 is deliberately left empty
because Engine / Goods Transfer has current sensing and no IR beam. One pin out of sixteen
buys a wiring map you can read off a terminal block without cross-referencing anything — "pin
6 is Engine Shed 1 on both boards" — and there are seven spare pins after it either way.

If you would rather pack board 2 solid, moving the three entries below pin 5 up by one is a
three-line config change. It costs the alignment.

## Board 1 — MCP23017 `0x20` — current sensing (`block_detection`)

| Pin | Port | Chip pin | Block | `sensorId` |
|---|---|---|---|---|
| 0 | GPA0 | 21 | Fiddle Yard 1 | `cs---fiddle-yard-1` |
| 1 | GPA1 | 22 | Fiddle Yard 2 | `cs---fiddle-yard-2` |
| 2 | GPA2 | 23 | Siding 1 | `cs---siding-1` |
| 3 | GPA3 | 24 | Siding 2 | `cs---siding-2` |
| 4 | GPA4 | 25 | Siding 3 | `cs---siding-3` |
| 5 | GPA5 | 26 | Engine / Goods Transfer | `cs---engine-goods-transfer` |
| 6 | GPA6 | 27 | Engine Shed 1 | `cs---engine-shed-1` |
| 7 | GPA7 | 28 | Engine Shed 2 | `cs---engine-shed-2` |
| 8 | GPB0 | 1 | Goods Shed | `cs---goods-shed` |
| 9–15 | GPB1–7 | 2–8 | *spare* | |

## Board 2 — MCP23017 `0x21` — IR (`ir_position`)

| Pin | Port | Chip pin | Block | `sensorId` |
|---|---|---|---|---|
| 0 | GPA0 | 21 | Fiddle Yard 1 | `ir---fiddle-yard-1` |
| 1 | GPA1 | 22 | Fiddle Yard 2 | `ir---fiddle-yard-2` |
| 2 | GPA2 | 23 | Siding 1 | `ir---siding-1` |
| 3 | GPA3 | 24 | Siding 2 | `ir---siding-2` |
| 4 | GPA4 | 25 | Siding 3 | `ir---siding-3` |
| 5 | GPA5 | 26 | **reserved** — Engine / Goods Transfer has no IR | — |
| 6 | GPA6 | 27 | Engine Shed 1 | `ir---engine-shed-1` |
| 7 | GPA7 | 28 | Engine Shed 2 | `ir---engine-shed-2` |
| 8 | GPB0 | 1 | Goods Shed | `ir---goods-shed` |
| 9–15 | GPB1–7 | 2–8 | *spare* | |

## Board 3 — MCP23017 `0x22` — point position feedback

**Two pins per point, not one.** A single input would infer `reverse` from the absence of
`normal`, and an absence is equally a broken wire, a lost supply, or a point sitting
mid-throw. The design and the hazards are in `docs/point-position-feedback.md`.

The pair for a point always stays on this one chip, so a chip that drops off the bus fails
both halves together and the point reads `unknown` — the correct answer. Split across two
chips, a half failure leaves exactly one closed reading: a confident, corroborated-looking
position that nothing is corroborating.

| Pin | Port | Chip pin | Point | Throw | `pointId` |
|---|---|---|---|---|---|
| 0 | GPA0 | 21 | P1 Fiddle Yard | normal | `88d5527d-c7db-40ab-b27b-532503487c32` |
| 1 | GPA1 | 22 | P1 Fiddle Yard | reverse | " |
| 2 | GPA2 | 23 | P2 Layout Entry | normal | `acc2150b-ffa1-4733-b2ff-1e4d2147dfe8` |
| 3 | GPA3 | 24 | P2 Layout Entry | reverse | " |
| 4 | GPA4 | 25 | P3 Siding 1 | normal | `8ccb1cf8-b9ce-448e-bb97-c56a886f46c3` |
| 5 | GPA5 | 26 | P3 Siding 1 | reverse | " |
| 6 | GPA6 | 27 | P4 Siding 2 | normal | `0e615557-a952-41ed-a5f2-c43d9eefa78f` |
| 7 | GPA7 | 28 | P4 Siding 2 | reverse | " |
| 8 | GPB0 | 1 | P5 Goods Shed | normal | `720bde49-5e2e-47f9-b691-1391e0195240` |
| 9 | GPB1 | 2 | P5 Goods Shed | reverse | " |
| 10 | GPB2 | 3 | P6 Engine Shed | normal | `210cf5c3-150a-4ef5-99b0-4a494eb48a4f` |
| 11 | GPB3 | 4 | P6 Engine Shed | reverse | " |
| 12–15 | GPB4–7 | 5–8 | *spare* | | |

Each input is pulled to 0 V when its side is made, so it reads 0 against the expander's
internal pull-up. That is the same polarity as the sensor boards, by coincidence rather than
by rule, which is why it has its own `POINTS_ACTIVE_LOW` flag.

**What drives these pins is undecided.** Neither Cobalt changeover is free: `S1` is tied to
the accessory bus and `S2` powers the frogs. **Never wire `S2` here**, because its common is
the frog. The candidates (tie-bar sensors, optos across the frog, an add-on changeover) all
present as closures to 0 V, so this allocation holds whichever is chosen. See
`point-position-feedback.md`, *The Cobalt has no free contact*.

**Which input is `normal` cannot be determined from the wiring.** Nothing reveals which way
round a point is fitted, or which input the orchestrator calls `normal`. It has to be
established per point by throwing it and looking. The `normal`/`reverse` columns above are the
allocation's *intent*; commissioning confirms or corrects them.

## Pin numbering is the thing that goes wrong

The numbers in these tables are **logical pins, 0-based**: 0–7 are GPA0–7, 8–15 are GPB0–7.
They are not header positions, and the two have already been confused twice — the Goods Shed
sensors landed first on logical 0 and then on logical 7 before reaching 8.

Two traps stacked on top of each other:

- **Logical 8 crosses to the other side of the chip.** Pins 0–7 are GPA0–7 on chip pins 21–28;
  pin 8 is GPB0 on chip **pin 1**. The ninth sensor on each board sits physically opposite the
  first eight. Worth knowing before the loom is cut.
- **Header labels are 1-based where these tables are 0-based**, so a header labelled `8` is
  logical pin 7.

If in doubt, do not count — measure. `mpremote run` a scan of all 32 pins with the sensor
triggered and see which one moves. That took under a minute and settled it definitively both
times counting got it wrong.

## Sensor polarity: per sensor

Polarity is a property of the device on the far end of the wire, not of the board or the
node, so every `SENSORS` entry in `config.py` carries its own `active_low`, set from a
per-device constant. There is no default: startup refuses an entry without one, or with
anything other than `True` or `False`.

Both fitted devices pull to ground when asserting — occupied on board 1, triggered on board
2 — with the internal pull-up holding the line high otherwise. **Occupied reads 0**, so both
constants are `True`. They are different devices that agree by coincidence, not by design.

The `bazauto/block-detection` board, built to replace the LM-iD.1 on board 1, is the first
that disagrees. It drives its output push-pull, 3.3 V when occupied, so it is
`BAZAUTO_BD_ACTIVE_LOW = False`. Against the pull-up, a broken signal wire from it reads
**occupied**, which is the point of it (#9). Moving a `cs---` entry onto it is a one-line
change, to make only after that channel's wire-pull check on the bench. The pull-up has to
stay on for those inputs: without it, the broken wire would float instead.

| Constant | Device | Occupied reads |
|---|---|---|
| `LM_ID_ACTIVE_LOW = True` | Legacy Models LM-iD.1 | 0 |
| `WAVESHARE_IR_ACTIVE_LOW = True` | Waveshare IR reflective | 0 |
| `BAZAUTO_BD_ACTIVE_LOW = False` | `bazauto/block-detection` rev 1.0 | 1 |

| Board | Device | Output |
|---|---|---|
| 1 (`0x20`) | Legacy Models LM-iD.1 | Open-drain. **Idles at 5 V if Input A is ever powered** — which is what every manufacturer datasheet tells you to do |
| 2 (`0x21`) | Waveshare IR reflective | LM393 open-collector with an on-board pull-up to its own 3.3 V VCC. Safe on an expander input directly |

**`docs/block-detector-wiring.md` is the whole picture**, and the place to read before
changing anything about how either is wired.

**A broken wire therefore reads as `clear`, not `occupied`** — the failure lands on the
permissive state, and the 30 s re-assert cannot catch it because the node is alive and happily
republishing. Accepted knowingly; see #9 for why, and for the closed-circuit alternatives —
one of which `docs/block-detector-wiring.md` shows is available after all.

On board 2 the same break reads as *beam not yet reached*, so a berthing run never gets its
stop trigger. Less likely to admit a train to occupied track; more likely to overrun a
position stop.

## Allocation is not installation

A pin appearing above means an address is reserved for it, **not** that anything is wired to
it. Only sensors listed as installed in the node's `config.py` are published.

This is a safety property, not tidiness. An unwired MCP23017 input sits pulled up, which
reads as **inactive**, which would publish `clear` — a confident assertion that a block is
empty, produced by a wire that does not exist. For a `block_detection` sensor that is enough
on its own to let the orchestrator clear the block (`domain/occupancy.ts`, clause 2).

So the node publishes an allow-list, never the whole table. Bringing a new sensor online is:
wire it to its allocated pin, add its id to the installed list, deploy.

## Currently installed

Two, both on the **Goods Shed** block:

| Board | Pin | Port | Chip pin | `sensorId` |
|---|---|---|---|---|
| 1 (`0x20`) | 8 | GPB0 | 1 | `cs---goods-shed` |
| 2 (`0x21`) | 8 | GPB0 | 1 | `ir---goods-shed` |

**No points.** Board 3 is fitted and answers on the bus, but no feedback source has been
chosen or wired, so `POINTS_INSTALLED` is empty and nothing is published on any `point/*/reading`. Bring points up
**one at a time**: every point fault kind Safe-Stops the whole layout, including a fault on a
point no route holds, so flipping all six to `positionFeedback: "required"` at once produces a
layout that halts on the first flaky contact with five other unproven points to rule out.

Two things follow from this being the pair brought up first, both of which paid off:

- **Goods Shed is the only block with both a CS and an IR sensor installed**, so the bring-up
  exercised the occupancy derivation rather than one sensor in isolation. That proved its
  worth immediately: the IR beam oscillates with a stationary loco over it (#10), and the
  block did not move once, because an `ir_position` `clear` is a no-op and only the CS sensor
  may assert a block is empty.
- **Both are on pin 8, which is GPB0** — the bank-1 side of the expander. That makes the first
  live deployment cover `mcp23017_io.py`'s bank selection and port-B registers, which the
  GPA-only path would have left untested on hardware.
