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

# Board 1 is current sensing, board 2 is IR.
EXPANDER_CS = 0x20
EXPANDER_IR = 0x21

# Measured on the bench 2026-08-23 with a loco standing in Goods Shed and obscuring the
# beam, so both sensors were known-occupied: both pins read 1, and still read 1 with the
# internal pull-up disabled, so both are driven rather than floating.
#
# The pre-split main.py had this as active-low, which inverts the meaning and would have
# published "clear" for an occupied block.
ACTIVE_LOW = False

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

# How long to wait between main-loop passes. Sensor edges are caught by polling the
# expanders, which are poll-only devices.
LOOP_SLEEP_MS = 50
