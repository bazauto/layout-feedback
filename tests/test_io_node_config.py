"""The IO node's real config, checked the way the node checks it at startup.

This is the test that matters most in the file set: it runs against the configuration
that actually deploys, so a typo in a sensor id or a pin clash fails here rather than as
silence on the broker.
"""

import config
from layout_mqtt import LayoutMQTT, validate_sensor_id
from point_wiring import assert_no_pin_overlap, select_installed_points
from sensor_wiring import select_installed


def test_the_real_config_resolves():
    selected = select_installed(config.SENSORS, config.INSTALLED)

    assert [e["sensor_id"] for e in selected] == list(config.INSTALLED)


def test_every_allocated_id_is_a_valid_contract_id():
    for entry in config.SENSORS:
        validate_sensor_id(entry["sensor_id"])


def test_the_layout_id_is_real():
    validate_sensor_id(config.LAYOUT_ID, what="layout id")
    assert config.LAYOUT_ID == "c4e587aa-478d-46ab-a1df-9dd8359fc040"


def test_both_installed_sensors_are_the_goods_shed_pair():
    """The one block with both a detector and a beam, so the bring-up exercises the
    occupancy derivation rather than one sensor in isolation."""
    assert set(config.INSTALLED) == {"cs---goods-shed", "ir---goods-shed"}


def test_installed_sensors_resolve_to_the_documented_pins():
    """docs/pin-allocation.md: both on pin 8, board 1 for CS and board 2 for IR."""
    by_id = {e["sensor_id"]: e for e in select_installed(config.SENSORS, config.INSTALLED)}

    assert (by_id["cs---goods-shed"]["expander"], by_id["cs---goods-shed"]["pin"]) == (0x20, 8)
    assert (by_id["ir---goods-shed"]["expander"], by_id["ir---goods-shed"]["pin"]) == (0x21, 8)


def test_pin_n_is_the_same_block_on_both_boards():
    """The alignment the allocation was designed around. Board 2 pin 5 is deliberately
    absent — Engine / Goods Transfer has current sensing and no IR beam."""
    cs = {e["pin"]: e["sensor_id"][len("cs---"):]
          for e in config.SENSORS if e["expander"] == config.EXPANDER_CS}
    ir = {e["pin"]: e["sensor_id"][len("ir---"):]
          for e in config.SENSORS if e["expander"] == config.EXPANDER_IR}

    assert 5 not in ir
    for pin, block in ir.items():
        assert cs[pin] == block, "pin %d names different blocks on the two boards" % pin


def test_sensors_are_active_low():
    """Switches to ground: closed when occupied, so occupied reads 0.

    Confirmed by scanning all 32 pins with a loco in the block and again with it
    removed. Note the consequence recorded in #9: a broken wire floats to the pull-up
    and therefore reads `clear`, which is the permissive state, not the safe one.
    """
    assert config.ACTIVE_LOW is True


def test_no_outputs_are_configured():
    """The contract has no output topic class, and this node drives nothing.

    Point *readings* are a separate matter — this node reports position, it does not
    command it. The motors take DCC accessory commands and cannot be driven from here.
    """
    assert tuple(config.OUTPUTS) == ()


# --- board 3: point position feedback ---------------------------------------

def test_the_real_point_config_resolves():
    selected = select_installed_points(config.POINTS, config.POINTS_INSTALLED)

    assert [e["point_id"] for e in selected] == list(config.POINTS_INSTALLED)


def test_no_points_are_installed_yet():
    """The expander is fitted and answers on the bus, but no S2 contacts are landed.

    An unwired pair reads both-open and would publish a confident `unknown` for a point
    nothing is watching — the same *allocation is not installation* rule as the sensors.
    """
    assert tuple(config.POINTS_INSTALLED) == ()


def test_every_allocated_point_id_is_a_valid_contract_id():
    for entry in config.POINTS:
        validate_sensor_id(entry["point_id"], what="point id")


def test_the_allocated_points_are_the_orchestrators_six():
    """Keyed on `points.id`, not a display name — they are independent fields upstream
    and a rename does not update the topic."""
    assert [e["point_id"] for e in config.POINTS] == [
        "88d5527d-c7db-40ab-b27b-532503487c32",  # P1 Fiddle Yard
        "acc2150b-ffa1-4733-b2ff-1e4d2147dfe8",  # P2 Layout Entry
        "8ccb1cf8-b9ce-448e-bb97-c56a886f46c3",  # P3 Siding 1
        "0e615557-a952-41ed-a5f2-c43d9eefa78f",  # P4 Siding 2
        "720bde49-5e2e-47f9-b691-1391e0195240",  # P5 Goods Shed
        "210cf5c3-150a-4ef5-99b0-4a494eb48a4f",  # P6 Engine Shed
    ]


def test_every_pair_shares_one_expander_and_is_adjacent():
    """A pair on one chip shares a failure domain: the chip drops off the bus, both
    halves read open, and the point reads `unknown` — the correct answer. Split across
    two chips, a half failure leaves exactly one closed reading, which looks corroborated
    and is not."""
    for entry in config.POINTS:
        assert entry["expander"] == config.EXPANDER_POINTS == 0x22
        assert entry["reverse_pin"] == entry["normal_pin"] + 1


def test_the_documented_allocation_is_what_deploys():
    """docs/point-position-feedback.md: six pairs, pins 0-11, 12-15 spare."""
    pins = []
    for entry in config.POINTS:
        pins.extend((entry["normal_pin"], entry["reverse_pin"]))

    assert pins == list(range(12))


def test_no_sensor_and_point_share_a_pin():
    assert_no_pin_overlap(list(config.SENSORS), list(config.POINTS)) is None


def test_point_contacts_are_active_low():
    """S2-C wired to 0 V against the expander pull-up, so a closed throw reads 0.

    Its own flag, not `ACTIVE_LOW`: a third device agreeing with the other two is a
    coincidence, and this repo has been bitten once by treating that as a rule.
    """
    assert config.POINTS_ACTIVE_LOW is True


def test_the_point_topics_the_node_will_use():
    """Spelled out in full, so a change to topic construction is visible in a diff."""
    layout = LayoutMQTT(_NullClient(), config.LAYOUT_ID)
    p1 = "88d5527d-c7db-40ab-b27b-532503487c32"

    assert layout.point_topic(p1) == (
        "layout/c4e587aa-478d-46ab-a1df-9dd8359fc040/point/"
        "88d5527d-c7db-40ab-b27b-532503487c32/reading")
    assert layout.point_query_topic(p1) == (
        "layout/c4e587aa-478d-46ab-a1df-9dd8359fc040/point/"
        "88d5527d-c7db-40ab-b27b-532503487c32/query")


def test_the_topics_the_node_will_publish():
    """Spelled out in full, so a change to topic construction is visible in a diff."""
    layout = LayoutMQTT(_NullClient(), config.LAYOUT_ID)

    assert layout.sensor_topic("cs---goods-shed") == (
        "layout/c4e587aa-478d-46ab-a1df-9dd8359fc040/sensor/cs---goods-shed/reading")
    assert layout.sensor_topic("ir---goods-shed") == (
        "layout/c4e587aa-478d-46ab-a1df-9dd8359fc040/sensor/ir---goods-shed/reading")


class _NullClient:
    def publish(self, topic, message, qos=0, retain=0):
        return True
