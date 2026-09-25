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


def test_every_installed_sensor_is_on_the_board_its_prefix_names():
    """`cs---` is current sensing on board 1, `ir---` is IR on board 2. A sensor on the
    wrong board would be read with the other device's polarity, upside down.

    Deliberately not a snapshot of INSTALLED: bringing a sensor online is a config change
    and should not also need a test edit."""
    board_for = {"cs---": config.EXPANDER_CS, "ir---": config.EXPANDER_IR}
    for entry in select_installed(config.SENSORS, config.INSTALLED):
        prefix = entry["sensor_id"][:len("cs---")]
        assert prefix in board_for, entry["sensor_id"]
        assert entry["expander"] == board_for[prefix], entry["sensor_id"]


def test_installed_sensors_are_not_duplicated():
    assert len(set(config.INSTALLED)) == len(config.INSTALLED)


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


def test_the_installed_sensors_have_their_devices_polarity():
    """Detectors are the bazauto/block-detection board, occupied reads 1; beams are
    Waveshare IR switches to ground, triggered reads 0."""
    expected = {config.EXPANDER_CS: config.BAZAUTO_BD_ACTIVE_LOW,
                config.EXPANDER_IR: config.WAVESHARE_IR_ACTIVE_LOW}
    assert config.BAZAUTO_BD_ACTIVE_LOW is False
    assert config.WAVESHARE_IR_ACTIVE_LOW is True
    for entry in select_installed(config.SENSORS, config.INSTALLED):
        assert entry["active_low"] is expected[entry["expander"]], entry["sensor_id"]


def test_every_current_sensor_uses_the_bazauto_detector_polarity():
    """Board 1 is all bazauto/block-detection. A `cs---` entry left on the LM-iD.1's
    polarity would read upside down the moment it is added to INSTALLED: an empty block
    published as occupied, and — worse — an occupied one as clear."""
    for entry in config.SENSORS:
        if entry["expander"] == config.EXPANDER_CS:
            assert entry["active_low"] is config.BAZAUTO_BD_ACTIVE_LOW, entry["sensor_id"]


def test_the_bazauto_detector_is_active_high():
    """Push-pull, 3.3 V when occupied, so a broken wire into the pull-up reads occupied."""
    assert config.BAZAUTO_BD_ACTIVE_LOW is False


def test_every_ir_sensor_uses_the_ir_polarity():
    """The bazauto/block-detection board replaces current sensing only. An IR entry
    given a detector's polarity would read every beam upside down."""
    for entry in config.SENSORS:
        if entry["expander"] == config.EXPANDER_IR:
            assert entry["active_low"] is config.WAVESHARE_IR_ACTIVE_LOW, entry["sensor_id"]


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
    """The expander is fitted and answers on the bus, but no feedback source is wired.

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
    """A made input is pulled to 0 V against the expander pull-up, so it reads 0.

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
