# Point position feedback — board 3

Design note for the third MCP23017 (`0x22`), which reads the Cobalt iP Digital point motors'
auxiliary switch contacts and publishes `point/{pointId}/reading`.

**Nothing here is built yet.** This is the design agreed before code, so that the wiring, the
allocation and the contract questions are settled while they are still cheap. The node work is
tracked separately; the one genuinely blocking question is in
`bazauto/layout-orchestration` and is named at the end.

The orchestrator's decision record for the feature is
`../layout-orchestration/docs/point-feedback.md` (D1–D10), and
`../layout-orchestration/docs/mqtt-contract.md` is **binding** on everything below.

## The shape of this is unusual: command and feedback are different devices

This is the fact to hold on to, because almost every hazard below follows from it.

```
   command                                    feedback
   ───────                                    ────────
   orchestrator                               Cobalt iP  S2 contacts
        │                                          │
        │ setPoint(dccAddress, position)           │  dry changeover
        ▼                                          ▼
   PicoDCC  ──<a ADDR SUBADDR ACTIVATE>──►    MCP23017 0x22
        │                                          │
        ▼                                          ▼
   Cobalt iP motor                            this node ──► MQTT
                                                            point/{id}/reading
```

The Cobalt iP is commanded over **DCC accessory addresses** and has **no feedback or query
mechanism of its own**. It cannot be asked anything. The only way its position is ever known
is the `S2` changeover contacts wired into this node.

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

`S1` on the Cobalt is spoken for by frog polarity. `S2` is a free SPDT changeover — common
plus two throws, break-before-make. Wire `S2-C` to 0 V and each throw to its own input:

| `S2-L` | `S2-R` | Reading | Meaning |
|---|---|---|---|
| closed | open | `normal` | corroborated |
| open | closed | `reverse` | corroborated |
| **open** | **open** | **`unknown`** | mid-travel, broken wire, or lost supply |
| closed | closed | `unknown` + fault | impossible on an SPDT — cross-wiring |

The fourth row is the whole argument for two inputs. It is a wiring error that announces
itself at commissioning instead of six months later, and it costs one expander pin.

**Two inputs is the ceiling, not a compromise.** The Cobalt exposes one free changeover, so
more pins per point cannot extract more truth from the motor. There is no version of this
design that benefits from a third input.

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

**Which throw is `normal` is unverifiable authored data.** Nothing can determine from the
wiring which way round a physical point is fitted, or which `S2` terminal corresponds to the
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

Flipping all six at once, on freshly wired microswitches, produces a layout that halts on the
first flaky contact with five other unproven points to rule out. Flip one, run it, leave it a
while. The escape hatch is per point and immediate: setting a point back to `'none'` clears
its latched fault.

## The blocking question, which is not ours

**Nothing checks that a point controller is still alive.** The pieces compound:

- `point/*/reading` is not retained and has **no periodic re-assert**, unlike `sensor/*/reading`
  with its 30 s rule. It is published on change and on query only.
- The confirmation deadline arms on a **`command`**; a **`query` deliberately does not** arm it.
- The backend queries on startup, on broker reconnect and on operator request — **not
  periodically**.

So once points are `'required'`, a node that dies quietly leaves the last `confirmedPosition`
standing indefinitely, and the layout keeps setting roads over points whose position nothing
has observed since it stopped. A dead *sensor* node degrades its blocks to `unknown` inside
the freshness window. A dead *point* node degrades nothing.

#25 left this open deliberately rather than by omission:

> Whether `sensor/*/reading` additionally needs its own liveness/staleness check remains #28's
> call, not this document's.

That is the sensor side. There is no equivalent on the point side at all.

**A periodic point re-assert is a `docs/mqtt-contract.md` amendment, and the contract changes
before the code.** It is not this repo's to make. Raised as an issue in
`bazauto/layout-orchestration`; this node should not be built against the current shape until
it is settled, because "publish every N seconds" versus "answer a periodic query" are
different firmware.
