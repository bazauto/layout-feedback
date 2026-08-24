# Block detector wiring — the LM-iD output stage

The current-sensing detectors on board 1 are **Legacy Models LM-iD.1** (Layout Concepts,
formerly DCC Concepts). This document exists because **none of what follows is published
anywhere** — the manufacturer's silkscreen, product page and application datasheets between
them do not state the output topology, and two bench sessions on 2026-08-23 reached opposite
conclusions about it from the same hardware.

Derived by measurement and by reading the application drawings, 2026-08-24. If you are about
to re-measure this, read the polarity section first — the answer depends on a wire you may
not realise is load-bearing.

## Terminal map

The board has three terminal groups. The silkscreen prints two ratings side by side against
one 4-way block, which reads as though it were a single output. It is not.

| Terminal | What it is |
|---|---|
| `L1` | The sensed conductor passes through here — the track dropper, in or out. Not a signal. |
| `A` | Power in. Marked `12V DC OR DCC`, `POLARITY FREE` — so there is a bridge rectifier behind it, and roughly 1.4 V of diode drop. |
| `B` | **The logic output.** Open-drain, active low. What this node uses. |
| `C` | `12V@250mA`, for relays and accessory decoders. Needs power at `A`. |

`B` and `C` share one 4-way block marked `(+) (−) (+) (−)`. **Every published application
drawing wires the lower pair, which is `C`** — the panel LED with its 1 k–5 k resistor, the
Cobalt iP `PBS-L/C/R` push-button inputs, the ESP receiver input jumpered to `3~20V`. Reading
those drawings and assuming they describe the logic output is the mistake this document
exists to stop; it is the reason `C` looks like a dry contact in the Cobalt drawing, which it
effectively is when referenced to the shared common.

## The output has two modes, selected by Input A

This is the fact that nothing published states, and the one that reconciles everything below.

| Input A | Clear | Occupied | Measured |
|---|---|---|---|
| **Unpowered** | open circuit (floats) | pulled to 0 V | 2026-08-24 |
| **Powered** | driven to **5 V** | pulled to 0 V | 2026-08-24 |

With `A` unpowered there is no internal rail to pull up to, so `B` is the bare output
transistor — a switch to ground and nothing else. With `A` powered, the same pin idles at
5 V from an internal pull-up. The vendor's *"the LM-iD will act without outside power, but
powering external devices needs a power source added"* is describing exactly this.

**Occupied reads 0 in both modes**, which is why `config.py`'s `ACTIVE_LOW = True` is correct
regardless of how `A` is wired, and why the node has never mis-reported occupancy over this.

## Why this repo held two contradictory conclusions

Both bench sessions were right about what they saw and wrong about what it meant:

- **#8** disabled the MCP23017's internal pull-up, found the pins still read 1, and concluded
  the sensors were actively driven high. That is what an LM-iD with `A` powered actually
  does.
- **#11** scanned all 32 pins, found every one read 1 including the thirty with nothing
  connected, and concluded the earlier evidence was an artifact of floating inputs and that
  the sensors are switches to ground. That is what an LM-iD with `A` unpowered actually does.

Neither reading was reproducible for the other because the mode had changed in between and
nobody knew there was a mode. **#11's conclusion is the one in the code and it is correct for
the layout as wired today.** The `active high` wording left behind in `CLAUDE.md` and in
`sensor_wiring.is_occupied`'s docstring was #8's, superseded and never removed; it is
corrected in the same change as this document.

The lesson is narrow and worth keeping: **measure `B` against 0 V with a meter, off the
expander**, not by reading a pin. An MCP23017 input cannot distinguish driven-high from
floating, so it cannot answer this question at all.

## The hazard when Input A is powered

**5 V on an MCP23017 input at `VDD` = 3.3 V is out of specification.** Absolute maximum on a
GPIO pin is `VDD + 0.6 V` = 3.9 V, so the clamp diode conducts continuously whenever a block
is clear — which is most of the time — and back-feeds the 3.3 V rail. It will appear to work.
It is still degrading the part.

`A` is **deliberately left unpowered** on Westgate Hollow as of 2026-08-24, which makes the
line 0 V–to–open and therefore harmless to the expander. That is a decision, not an accident,
and it is load-bearing:

> **Every manufacturer datasheet tells you to wire `A` to the DCC bus**, and doing so is also
> how you get the onboard detection LED working. Someone tidying the wiring in a year,
> following the official drawings, would put 5 V onto sixteen 3.3 V inputs without realising
> anything had changed.

If `A` is ever powered, the inputs need protecting first. Three options, cheapest first:

1. **A 4.7 k–10 k pull-up to 3.3 V per channel** does *not* solve this — it is worth fitting
   anyway (see below), but it does not stop the 5 V.
2. **Level shifter** — the 4-channel BSS138 board (Adafruit 757, the *I²C-safe* one) is built
   for open-drain sources with pull-ups and works here. **Not** the TXB0108 (Adafruit 395):
   its auto-direction sensing is defeated by exactly this kind of source, and Adafruit's own
   note says it is not for driving long cables.
3. **74LVC14A** — hex inverting Schmitt trigger with 5 V-tolerant inputs, run at 3.3 V. Takes
   the 5 V directly with no shifter in the path, and the hysteresis is worth having on
   layout-length runs beside a DCC bus. Six channels per package. This is the option that
   also buys supervision — see below.

## Noise immunity, unrelated to any of the above

With `A` unpowered there is **no pull-up anywhere on the line** except the MCP23017's internal
~100 kΩ. That is a high-impedance node running the length of the layout alongside a DCC bus.
**Fit an external 4.7 k–10 k pull-up to 3.3 V per channel** and disable the internal one in
`GPPU`. One resistor per channel, no downside, and it is the single highest-value change
available here. Phantom occupancy on a long run is the failure it prevents.

## What this changes about #9

[#9](https://github.com/bazauto/layout-feedback/issues/9) — *a broken sensor wire reads as
clear track* — was closed as **accepted knowingly** on 2026-08-23. It rejected the
closed-circuit option on this basis:

> **A normally-closed output** — circuit closed when the block is clear, opening when
> occupied. Broken wire then reads occupied. **Not available on the current sensors**
> (checked 2026-08-23); they are electronic switches, not relay contacts.

**That premise is false.** With `A` powered, `clear` is a *driven 5 V assertion*, not an
absence — and a severed wire produces neither 5 V nor 0 V. The three states become
distinguishable:

| State | Line at the node |
|---|---|
| Clear | ~5 V |
| Occupied | 0 V |
| **Wire broken / detector dead** | **floating** |

A 100 kΩ pull-**down** at the node end plus an inverting stage resolves the float into the
restrictive answer:

```
LM-iD B ──┬── 74LVC14A input ──► MCP23017 input
          └── 100k ── 0V
```

- Clear → ~5 V in → output **low**
- Occupied → 0 V in → output **high**
- Broken wire → pull-down wins → 0 V in → output **high** → **reads occupied**

That is the closed-circuit principle #9 was reaching for, achieved with one 14-pin package
per six channels and no change to the detectors. It requires `A` to be powered, and it
requires `ACTIVE_LOW` to be flipped to `False`, because the inverter has moved the polarity.

**The important half is not the circuit, it is what it means:** powering `A` is what turns
`clear` from an *absence of signal* into an *assertion*. With `A` unpowered, clear and
broken are electrically the same state and no amount of pull-up choice, inversion or firmware
can separate them — the information is not on the wire. This is the same principle as the
orchestrator's sensor-trust rule one layer down: a retained reading is not evidence, an empty
payload asserts nothing, and neither does an open circuit.

Current position is unchanged — `A` stays unpowered while the layout is being brought up, and
the broken-wire limitation stands as #9 describes it. This document records that the door #9
believed was shut is open, and what it costs to walk through it.

## Sources

The measurements are the authority. These are what the reasoning was checked against:

- [LM-iD.1 product page and application datasheets](https://www.layoutconcepts.com/lm-id-1) —
  `Block Detection Using LM-iD With LED Indication`, `ESP Receiver & LM-iD Block Detectors`,
  and `Auto Point Switching Using LM-iDs` are the three that show the output wiring, all of
  them wiring `C`.
- [Instruction-card text carrying the `OUTPUT B` / `OUTPUT C` split](https://railsofsheffield.com/products/dcc-concepts-lm-id-1-legacy-models-intelligent-detector-single-pack)
  — the only place the two outputs are described separately.
- [MCP23017 datasheet](https://ww1.microchip.com/downloads/en/DeviceDoc/20001952C.pdf) —
  absolute maximum ratings.
- [74LVC14A datasheet](https://assets.nexperia.com/documents/data-sheet/74LVC14A.pdf) — the
  5 V-tolerant input.
