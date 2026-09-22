# Point position feedback — board 3

Design note for the third MCP23017 (`0x22`), which reads two feedback inputs per point motor
and publishes `point/{pointId}/reading`.

**Status (2026-09-22): the firmware is built; the feedback source is undecided.** The expander
is fitted and answers on the bus, `POINTS_INSTALLED` in `config.py` is empty, and nothing is
published. The design as built assumed the Cobalt's `S2` changeover was free to read. **It is
not: `S2` powers the frogs**, so there is currently nothing to wire. See *The Cobalt has no
free contact*. The blocking question below settled before the code was written — see *The
blocking question, which was not ours*.

The orchestrator's decision record for the feature is
`../layout-orchestration/docs/point-feedback.md` (D1–D10), and
`../layout-orchestration/docs/mqtt-contract.md` is **binding** on everything below.

## The shape of this is unusual: command and feedback are different devices

This is the fact to hold on to, because almost every hazard below follows from it.

```
   command                                    feedback
   ───────                                    ────────
   orchestrator                               feedback pair (undecided)
        │                                          │
        │ setPoint(dccAddress, position)           │  two closures to 0 V
        ▼                                          ▼
   PicoDCC  ──<a ADDR SUBADDR ACTIVATE>──►    MCP23017 0x22
        │                                          │
        ▼                                          ▼
   Cobalt iP motor                            this node ──► MQTT
                                                            point/{id}/reading
```

The Cobalt iP is commanded over **DCC accessory addresses** and has **no feedback or query
mechanism of its own**. It cannot be asked anything. The only way its position is ever known
is a pair of feedback inputs wired into this node, and the motor itself has none left to give
(*The Cobalt has no free contact*).

So the contract's single logical "point controller" is physically **two unrelated devices on
two different transports, which do not know about each other.** The thing that receives the
command is not the thing that reports the position.

Two consequences, one good and one dangerous:

- **Good: a hand-thrown point is detected.** Because feedback is independent of command,
  a point thrown by hand, knocked, or drifting on a failing linkage shows up as an
  uncommanded position change — `confirmation: 'mismatch'`. A controller that self-reported
  would have no idea.
- **Dangerous: nothing correlates the two ends.** See *The mapping hazard* below.

## Two inputs per point, not one

Settled by the contract, which already assumed it:

> `position` — `"normal"` | `"reverse"` | `"unknown"`. Observed position. `"unknown"` =
> cannot determine (no sensor, **both sensors open**, mid-travel).

A single input cannot produce that three-state reading. It would infer `reverse` from the
absence of `normal` — and an absence is equally a broken wire, a lost supply, a point sitting
mid-throw, or a linkage that has dropped off. Publishing `source: "sensor"` on that basis is
the exact failure `point-feedback.md` D3 warns about:

> Accepting a `driver` reading as `confirmed` on a `'required'` point would let a firmware
> author who reports `"sensor"` out of optimism — or who never wired the feedback switch at
> all — silently defeat the whole feature.

Whatever the source, each point presents two inputs, each pulled to 0 V when its side is
made: a contact closing, an opto conducting, a Hall switch operating.

| Normal input | Reverse input | Reading | Meaning |
|---|---|---|---|
| closed | open | `normal` | corroborated |
| open | closed | `reverse` | corroborated |
| **open** | **open** | **`unknown`** | mid-travel, broken wire, or lost supply |
| closed | closed | `unknown` + fault | impossible if both sense one mechanism — cross-wiring |

The fourth row is the whole argument for two inputs. It is a wiring error that announces
itself at commissioning instead of six months later, and it costs one expander pin.

## The Cobalt has no free contact

**Found 2026-09-22, after the firmware was built.** The Cobalt iP Digital has two
break-before-make changeovers, and neither is available to this node:

- **`S1` is tied to the motor's own power input.** DCC Concepts describe it as "directly
  linked to the power input wires", intended to feed the frog. On this layout the motors run
  from a **separate accessory DCC bus**, deliberately: a loco running into a wrongly set
  point shorts the track bus, and the fix for that short is to throw the point, so the motor
  must stay powered through it. `S1` would therefore feed the frog from the wrong bus. It is
  also DCC-referenced, so it could never be a 3.3 V feedback contact either.
- **`S2`, the independent changeover, powers the frogs** from the track bus. It is the only
  way to do that correctly here, and it is spoken for.

So the design's premise, "`S2` is free", was wrong. The truth table, the pair allocation and
every line of firmware are unaffected **as long as the replacement presents two independent
closures to 0 V**, and all the candidates below can. `S2` itself must **never** be wired to
the expander: its common is the frog, and tying it to 0 V shorts a stock rail to logic
ground.

### Candidate feedback sources

Undecided. The choice depends on what the physical layout allows under each point.

**A. Tie-bar or blade sensors, two per point.** Two microswitches, or two Hall-effect
switches with a magnet on the tie-bar, one at each end of travel.

- Reads **the blades**, which is what the backend's `confirmed` actually asserts. A dropped
  linkage or a blade held off by debris shows as `unknown` or `mismatch`; the other options
  report the motor or the frog and would call it `confirmed`.
- Fully isolated from both DCC buses, and reads with track power off.
- Cost: mechanical fitting per point, under the baseboard. A Hall part must be rated for a
  3.3 V supply (the common A3144 needs 4.5 V) and be open-collector, so it looks like a
  contact to 0 V.

**B. Optos across the frog, sensing `S2`'s output.** One opto from the frog to each stock
rail. The rail the frog is bonded to has no voltage across its opto; the other sees full
track DCC and conducts. The phototransistor pulls its expander input to 0 V.

- No mechanical work, and it fits the truth table directly: the frog is dead mid-throw
  (break-before-make), so both go dark, which is the honest `unknown`.
- DCC is bipolar, so the opto needs an AC input (H11AA1 or PC814 class) or an anti-parallel
  diode, plus a series resistor sized for track voltage.
- Reads **frog polarity, not blades**. It confirms `S2` moved, not that the point did.
- **It reads nothing without track power.** Every point goes `unknown` whenever the track bus
  is off, and the accessory bus exists for exactly that case: a loco shorts the track bus at
  a wrongly set point, the booster cuts out, and the point is thrown to clear it. The throw
  then cannot be confirmed, because the feedback died with the track bus. On a point at
  `positionFeedback: 'required'` that is a confirmation `timeout`, which Safe-Stops. How the
  backend treats a Safe Stop, a track power-off and a point `unknown` together has to be
  checked in `bazauto/layout-orchestration` before this option is safe to rely on.

**C. An add-on changeover driven by the Cobalt's linkage.** A separate microswitch or slide
switch actuated by the motor's output. Isolated and power-independent like A, but reports the
motor rather than the blades, and needs a mount per point.

**Two inputs is the ceiling, not a compromise.** Whatever the source, two independent
closures give every state the contract defines. There is no version of this design that
benefits from a third input.

References: DCC Concepts' [Cobalt iP Digital product page](https://www.dccconcepts.com/products/cobalt-ip-digital/)
for the `S1`/`S2` description; a [DCC Concepts forum thread](https://www.dccconceptsforum.com/post/what-triggers-the-cobalt-ip-digital-frog-connector-to-switch-polarity-11281491)
confirming `S1`'s frog polarity follows the motor's DCC input wires; and
[US 9,662,591](https://patents.justia.com/patent/9662591), which describes Hall switches and a
tie-bar magnet for exactly option A.

## Both inputs of a point stay on the same expander

Ideally in the same port byte. A pair on one chip is read in one I2C transaction and, more
importantly, **shares a failure domain**: if the chip drops off the bus, both halves fail
together, both read open, and the point reads `unknown` — the correct answer.

Split a pair across two chips and a *half* failure leaves one live input and one dead one:
exactly one closed reading, which is a confident, corroborated-looking `normal` or `reverse`
that nothing is corroborating. The two-input check silently degrades into the one-input
design it was meant to replace.

There are spare boards available, so there is no reason to spread. Six points fit on one.

## Allocation — MCP23017 `0x22`

Pairs are adjacent and never straddle the `GPA`/`GPB` boundary (pins 0–7 are GPA0–7 on chip
pins 21–28; pin 8 is GPB0 on chip **pin 1**).

| Point | `pointId` | DCC addr | Normal pin | Reverse pin |
|---|---|---|---|---|
| P1 - Fiddle Yard | `88d5527d-c7db-40ab-b27b-532503487c32` | 11 | 0 | 1 |
| P2 - Layout Entry | `acc2150b-ffa1-4733-b2ff-1e4d2147dfe8` | 12 | 2 | 3 |
| P3 - Siding 1 | `8ccb1cf8-b9ce-448e-bb97-c56a886f46c3` | 13 | 4 | 5 |
| P4 - Siding 2 | `0e615557-a952-41ed-a5f2-c43d9eefa78f` | 14 | 6 | 7 |
| P5 - Goods Shed | `720bde49-5e2e-47f9-b691-1391e0195240` | 15 | 8 | 9 |
| P6 - Engine Shed | `210cf5c3-150a-4ef5-99b0-4a494eb48a4f` | 16 | 10 | 11 |

Pins 12–15 spare. The DCC address column is **not** used by this node — it is here only so
the two halves of the system can be checked against each other by eye.

This table lands in `config.py` when the node code does, under the same *allocation is not
installation* rule as the sensors: only points listed as installed are published, because an
unwired input pair reads both-open, which would publish a confident `unknown` for a point
nothing is watching.

**Which input is `normal` is unverifiable authored data.** Nothing can determine from the
wiring which way round a physical point is fitted, or which feedback input corresponds to the
orchestrator's `normal`. It must be established per point by throwing it and looking, exactly
as `../layout-orchestration/docs/track-grid.md` D9 says of a point tile's leg mapping. Assume
nothing from the terminal labels.

## The mapping hazard

**This is the one to design against.** Two independent mappings decide which physical motor
is which, and they are maintained in different repositories:

| Mapping | Lives in | Decides |
|---|---|---|
| `points.dcc_address` → motor | orchestrator DB | which motor gets **commanded** |
| `pointId` → expander pins | this node's `config.py` | which motor gets **read** |

Nothing checks that they agree. If either is wrong, the orchestrator commands motor X and
reads back motor Y's position — and **both ends look healthy.** Confirmation will even
*succeed* whenever X and Y happen to be sitting in the same position, which for a
just-commissioned layout with everything normalised is most of the time.

This is the same shape as the existing trap about keying config on the topic id rather than a
display name, and it is worse, because the failure is intermittent by construction.

**The commissioning check that catches it:** throw each point individually, and confirm that
the expected feedback pair — **and only that pair** — moves. Testing all six by throwing them
together proves nothing. Testing one while the others sit in matching positions proves nothing
either. One at a time, watching for movement on pins that should *not* change.

## What the node has to do

- **Publish `point/{pointId}/reading`** on every observed change, with `source: "sensor"`.
  This node never has `driver` knowledge — it does not command anything — so it must never
  publish `source: "driver"`.
- **Publish the transient `unknown`.** A break-before-make changeover genuinely passes through
  both-open on every throw, so the honest sequence is `normal` → `unknown` → `reverse`. The
  backend expects it and the confirmation timeout is 8000 ms (D5), so a normal throw lands
  well inside. Do not debounce it away and do not suppress it to keep the log tidy.
- **Subscribe to `point/{pointId}/query` and answer it live.** New behaviour for this repo —
  the io-node only publishes today. The backend queries on startup, on broker reconnect and
  on operator request. Answering late is safe: a `command` arms the confirmation deadline and
  a **`query` deliberately does not**. Not answering at all leaves the point at `unknown` and
  every edge through it untraversable.
- **Publish the orchestrator's `points.id` UUID**, not the display name. `point-feedback.md`
  accepts a compile-time lookup table for now; that is the allocation above.
- **Never retain.** `point/*/reading` is explicitly not retained — a point's position can
  change while its controller is offline, so a retained reading is a confident assertion with
  no correction path.

Run `/mqtt-check` against the diff. The contract is owned in `layout-orchestration` and is
binding here.

## Bring it up one point at a time

Every one of the five point fault kinds — `timeout`, `mismatch`, `indeterminate`,
`malformed-payload`, `id-mismatch` — **Safe-Stops the whole layout**, including a fault on a
point no route holds (D4). All six points are `position_feedback: 'none'` today, so wiring the
switches changes nothing until they are flipped to `'required'`.

Flipping all six at once, on freshly wired feedback, produces a layout that halts on the
first flaky contact with five other unproven points to rule out. Flip one, run it, leave it a
while. The escape hatch is per point and immediate: setting a point back to `'none'` clears
its latched fault.

## The blocking question, which was not ours — settled

**Nothing checked that a point controller was still alive.** `point/*/reading` was not
retained and had no periodic re-assert, the confirmation deadline arms on a `command` and
never on a `query`, and the backend queried only on startup, on reconnect and on operator
request. So a node that died quietly left the last `confirmedPosition` standing indefinitely
while the layout kept setting roads over points nothing had observed since.

The topology sharpened that rather than softening it: because the commanded device is not the
reporting device, silence after a command carried **no signal at all** — a DCC accessory
command is fire-and-forget and keeps succeeding long after the feedback node has stopped.

This was a `docs/mqtt-contract.md` amendment and not this repo's to make. Raised as
[`bazauto/layout-orchestration#167`](https://github.com/bazauto/layout-orchestration/issues/167),
**closed 2026-08-24**. It settled as the first of the two candidate answers — republish on a
timer, which is the same shape this repo already runs for sensors, rather than a periodic
query. The contract now reads:

> Published by the point controller whenever its observed position changes, in response to a
> `point/{pointId}/query`, **and** re-published unchanged at least every **30 seconds** as a
> liveness assertion. NOT retained.

with `POINT_FRESHNESS_TIMEOUT_MS` at 90 s — three missed re-asserts. A `required` point that
goes quiet degrades to `confirmation: "stale"` with `confirmedPosition: "unknown"` and its
edges become untraversable. That is a **degradation, not a fault**: it latches no `PointFault`
and does not Safe-Stop. A point left at `positionFeedback: "none"` has nothing reporting on it
and never goes stale.

So this node re-asserts every point on the same 25 s timer as its sensors, through the same
`LayoutMQTT.tick()`. One rule, one loop — the re-assert is the single easiest thing here to
forget and the most expensive to get wrong, and it should not be possible to implement it for
one topic and miss it for the other.

## Where this lives in the code

| Piece | File |
|---|---|
| Contact pair → position, and the allocation checks | `src/lib/point_wiring.py` |
| Topics, payload, QoS/retention, the re-assert, query bookkeeping | `src/lib/layout_mqtt.py` |
| The `0x22` allocation and the installed allow-list | `src/apps/io-node/config.py` |
| Pin setup, query subscription, the read loop | `src/apps/io-node/main.py` |

Two things worth knowing about the implementation:

- **A query is noted, never answered from the callback.** `poll()` dispatches the handler, so
  publishing from inside it would re-enter the modem in the middle of whatever command
  `poll()` was called from. `note_query()` sets a flag and `tick()` answers on the next pass,
  a few tens of milliseconds later. Safe to be late, because a query does not arm the
  confirmation deadline.
- **A failed subscribe refuses to start.** Unlike a failed publish, it is not survivable: the
  node would come up healthy, re-assert happily, and never answer a query — leaving every
  queried point at `unknown` with nothing reporting a fault. It raises, the supervisor retries,
  and the LED flashes code 5.
