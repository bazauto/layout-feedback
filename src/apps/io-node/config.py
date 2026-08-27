"""Wiring and identity for the IO node. The only board-specific file.

Nothing under `lib/` hardcodes a pin, an address, or a layout id — it all lives here, so
moving this node to different hardware is one file.

`SENSORS` is the **allocation**: every sensor the layout has, and which expander pin it
owns. `INSTALLED` is what is actually wired, and **only those are published.**

That distinction is a safety property, not tidiness. An unwired MCP23017 input sits
pulled up, reads as inactive, and would publish `clear` — a confident assertion that a
block is empty, produced by a wire that does not exist. For a `block_detection` sensor
that alone lets the orchestrator clear the block (`domain/occupancy.ts`, clause 2).
Publishing the whole table would put fifteen of those on the broker.

Bringing a sensor online: wire it to its allocated pin, add its id to `INSTALLED`,
deploy. See `docs/pin-allocation.md`.
"""

# From the orchestrator's Configure > Sensors. Topics are layout/{LAYOUT_ID}/...
LAYOUT_ID = "c4e587aa-478d-46ab-a1df-9dd8359fc040"

# Distinct per node. Two clients sharing an MQTT client id knock each other off the
# broker in a reconnect loop that looks exactly like flaky hardware.
MQTT_CLIENT_ID = "layout-feedback-io-node"

MQTT_HOST = "172.18.10.240"
MQTT_PORT = 1883
MQTT_UART_ID = 1
MQTT_TX_PIN = 8
MQTT_RX_PIN = 9
MQTT_BAUD = 9600

# I2C0, shared by both expanders.
I2C_ID = 0
I2C_SDA = 4
I2C_SCL = 5

# Board 1 is current sensing, board 2 is IR, board 3 is point position feedback.
EXPANDER_CS = 0x20
EXPANDER_IR = 0x21
EXPANDER_POINTS = 0x22

# The sensors are switches to ground: closed (conducting) when the block is occupied,
# open otherwise, with the MCP23017's internal pull-up holding the line high when open.
# So occupied reads 0. Confirmed on the bench 2026-08-23 by scanning all 32 pins with a
# loco in Goods Shed and again with it removed — pin 8 on both boards was the only one
# that moved.
#
# **A broken wire reads as `clear`, not `occupied`** (#9). The pull-up gives a defined
# level on an open circuit, but that level is the permissive one, so a severed wire
# asserts empty track and the re-assert keeps confirming it. Accepted knowingly for now;
# see #9 for the closed-circuit alternatives.
ACTIVE_LOW = True

DEBOUNCE_MS = 200

# The full allocation. Pin N is the same block on both boards; board 2 pin 5 is reserved
# and empty because Engine / Goods Transfer has current sensing and no IR beam.
SENSORS = (
    # --- board 1, current sensing (block_detection) ---
    {"sensor_id": "cs---fiddle-yard-1", "expander": EXPANDER_CS, "pin": 0},
    {"sensor_id": "cs---fiddle-yard-2", "expander": EXPANDER_CS, "pin": 1},
    {"sensor_id": "cs---siding-1", "expander": EXPANDER_CS, "pin": 2},
    {"sensor_id": "cs---siding-2", "expander": EXPANDER_CS, "pin": 3},
    {"sensor_id": "cs---siding-3", "expander": EXPANDER_CS, "pin": 4},
    {"sensor_id": "cs---engine-goods-transfer", "expander": EXPANDER_CS, "pin": 5},
    {"sensor_id": "cs---engine-shed-1", "expander": EXPANDER_CS, "pin": 6},
    {"sensor_id": "cs---engine-shed-2", "expander": EXPANDER_CS, "pin": 7},
    {"sensor_id": "cs---goods-shed", "expander": EXPANDER_CS, "pin": 8},
    # --- board 2, IR (ir_position) ---
    {"sensor_id": "ir---fiddle-yard-1", "expander": EXPANDER_IR, "pin": 0},
    {"sensor_id": "ir---fiddle-yard-2", "expander": EXPANDER_IR, "pin": 1},
    {"sensor_id": "ir---siding-1", "expander": EXPANDER_IR, "pin": 2},
    {"sensor_id": "ir---siding-2", "expander": EXPANDER_IR, "pin": 3},
    {"sensor_id": "ir---siding-3", "expander": EXPANDER_IR, "pin": 4},
    # pin 5 reserved — no IR beam on Engine / Goods Transfer
    {"sensor_id": "ir---engine-shed-1", "expander": EXPANDER_IR, "pin": 6},
    {"sensor_id": "ir---engine-shed-2", "expander": EXPANDER_IR, "pin": 7},
    {"sensor_id": "ir---goods-shed", "expander": EXPANDER_IR, "pin": 8},
)

# Physically wired, and therefore the only sensors published. Both on Goods Shed, which
# is the one block with both a detector and a beam — so the bring-up exercises the
# occupancy derivation rather than one sensor in isolation.
INSTALLED = (
    "cs---goods-shed",
    "ir---goods-shed",
)

# None on this layout: points are controlled via DCC, and the orchestrator has no output
# topics. The expander's output support is kept in lib/ for when that changes.
OUTPUTS = ()

# --- board 3 (0x22): point position feedback ------------------------------
#
# The Cobalt iP Digital motors are commanded over **DCC** and have no feedback path of
# their own — they cannot be asked anything. Position is read only from each motor's
# free `S2` changeover, wired `S2-C` to 0 V with each throw to its own input here.
#
# Two inputs per point, never one. A single input would infer `reverse` from the absence
# of `normal`, and an absence is equally a broken wire, a lost supply, or a point sitting
# mid-throw. See docs/point-position-feedback.md.
#
# `S2-C` to 0 V against the expander's pull-up means a closed throw reads 0. Its own flag
# rather than `ACTIVE_LOW` above: this is a third device that happens to agree with the
# other two, and treating that coincidence as a rule is a mistake this repo has already
# made once.
POINTS_ACTIVE_LOW = True

# Contact bounce on the changeover. Safe at this value because a Cobalt iP takes seconds
# to travel, so the genuine both-open window during a throw is far wider than the
# debounce and is still reported. It is a separate knob from DEBOUNCE_MS so that tuning
# the sensors cannot silently swallow the `normal` -> `unknown` -> `reverse` sequence the
# backend expects — suppressing that transient is the mistake to avoid here.
POINT_DEBOUNCE_MS = 200

# The full allocation, from docs/point-position-feedback.md. Pairs are adjacent and stay
# on one expander, so a chip that drops off the bus fails both halves together and the
# point reads `unknown` — the correct answer. Split across two chips, a half failure
# would leave exactly one closed reading: a confident, corroborated-looking position that
# nothing is corroborating.
#
# The `dcc_address` comment on each row is **not used by this node**. It is here only so
# the two halves of the system can be checked against each other by eye: which motor gets
# commanded lives in the orchestrator's `points.dcc_address`, which motor gets read lives
# here, and nothing cross-checks them.
#
# **Which throw is `normal` is unverifiable authored data.** Nothing in the wiring reveals
# which way round a point is fitted or which `S2` terminal the orchestrator calls
# `normal`. It must be established per point by throwing it and looking — assume nothing
# from the terminal labels.
POINTS = (
    # point_id (orchestrator points.id)          dcc  normal  reverse
    {"point_id": "88d5527d-c7db-40ab-b27b-532503487c32",  # P1 Fiddle Yard,   dcc 11
     "expander": EXPANDER_POINTS, "normal_pin": 0, "reverse_pin": 1},
    {"point_id": "acc2150b-ffa1-4733-b2ff-1e4d2147dfe8",  # P2 Layout Entry,  dcc 12
     "expander": EXPANDER_POINTS, "normal_pin": 2, "reverse_pin": 3},
    {"point_id": "8ccb1cf8-b9ce-448e-bb97-c56a886f46c3",  # P3 Siding 1,      dcc 13
     "expander": EXPANDER_POINTS, "normal_pin": 4, "reverse_pin": 5},
    {"point_id": "0e615557-a952-41ed-a5f2-c43d9eefa78f",  # P4 Siding 2,      dcc 14
     "expander": EXPANDER_POINTS, "normal_pin": 6, "reverse_pin": 7},
    {"point_id": "720bde49-5e2e-47f9-b691-1391e0195240",  # P5 Goods Shed,    dcc 15
     "expander": EXPANDER_POINTS, "normal_pin": 8, "reverse_pin": 9},
    {"point_id": "210cf5c3-150a-4ef5-99b0-4a494eb48a4f",  # P6 Engine Shed,   dcc 16
     "expander": EXPANDER_POINTS, "normal_pin": 10, "reverse_pin": 11},
)

# Physically wired, and therefore the only points published. **Empty: the expander is
# fitted and answers on the bus, but no `S2` contacts are landed yet.**
#
# Bring one up at a time. Every point fault kind Safe-Stops the whole layout, including a
# fault on a point no route holds, so flipping all six to `positionFeedback: 'required'`
# in the orchestrator at once produces a layout that halts on the first flaky contact with
# five other unproven points to rule out.
#
# The commissioning check that catches the mapping hazard: throw each point individually
# and confirm the expected pair — and only that pair — moves. Throwing them together
# proves nothing, and neither does testing one while the others sit in matching positions.
POINTS_INSTALLED = ()

# How long to wait between main-loop passes. Sensor edges are caught by polling the
# expanders, which are poll-only devices.
LOOP_SLEEP_MS = 50
