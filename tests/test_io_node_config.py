"""The IO node's real config, checked the way the node checks it at startup.

This is the test that matters most in the file set: it runs against the configuration
that actually deploys, so a typo in a sensor id or a pin clash fails here rather than as
silence on the broker.
"""

import config
from layout_mqtt import LayoutMQTT, validate_sensor_id
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
    """Points are DCC-controlled and the contract has no output topic class."""
    assert tuple(config.OUTPUTS) == ()


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
