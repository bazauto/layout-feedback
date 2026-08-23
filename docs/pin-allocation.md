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

**Pin 8 crosses to the other side of the chip.** Pins 0–7 are GPA0–7 on chip pins 21–28; pin 8
is GPB0 on chip pin 1. The ninth sensor on each board is physically opposite the first eight.
Worth knowing before the loom is cut.

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

Two things follow from this being the pair brought up first, both of them useful:

- **Goods Shed is the only block with both a CS and an IR sensor installed**, so the bring-up
  exercises the occupancy derivation rather than one sensor in isolation. The IR beam reading
  `clear` must be a no-op, and only the CS sensor may clear the block. If breaking the beam
  alone ever clears Goods Shed, something is wrong upstream and the bring-up has found it.
- **Both are on pin 8, which is GPB0** — the bank-1 side of the expander. That makes the first
  live deployment cover `mcp23017_io.py`'s bank selection and port-B registers, which the
  GPA-only path would have left untested on hardware.
