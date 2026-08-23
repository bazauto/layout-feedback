"""Config mistakes must fail at startup, not become silence on the broker.

Every check here catches something that otherwise leaves the node looking perfectly
healthy while publishing the wrong thing, or nothing.
"""

import pytest

from sensor_wiring import is_occupied, select_installed

TABLE = (
    {"sensor_id": "cs---goods-shed", "expander": 0x20, "pin": 8},
    {"sensor_id": "ir---goods-shed", "expander": 0x21, "pin": 8},
    {"sensor_id": "cs---siding-1", "expander": 0x20, "pin": 2},
)


def test_only_installed_sensors_are_selected():
    """The allow-list is the safety property: an unwired input reads as inactive and
    would publish `clear` for a block nothing is watching."""
    selected = select_installed(TABLE, ("cs---goods-shed",))

    assert [entry["sensor_id"] for entry in selected] == ["cs---goods-shed"]


def test_selection_preserves_installed_order():
    selected = select_installed(TABLE, ("ir---goods-shed", "cs---siding-1"))

    assert [e["sensor_id"] for e in selected] == ["ir---goods-shed", "cs---siding-1"]


def test_the_same_pin_on_different_expanders_is_fine():
    """Pin N is deliberately the same block on both boards."""
    selected = select_installed(TABLE, ("cs---goods-shed", "ir---goods-shed"))

    assert len(selected) == 2


def test_an_installed_id_missing_from_the_table_is_refused():
    """A typo here publishes to a topic nobody subscribes to, in silence."""
    with pytest.raises(ValueError, match="not in SENSORS"):
        select_installed(TABLE, ("cs---goods-shedd",))


def test_two_sensors_on_one_pin_are_refused():
    """At least one of them would be reporting another block's state, and nothing on
    the broker would show it."""
    clashing = TABLE + ({"sensor_id": "cs---siding-2", "expander": 0x20, "pin": 8},)

    with pytest.raises(ValueError, match="both on expander"):
        select_installed(clashing, ())


def test_a_duplicate_sensor_id_is_refused():
    duped = TABLE + ({"sensor_id": "cs---goods-shed", "expander": 0x21, "pin": 3},)

    with pytest.raises(ValueError, match="twice in SENSORS"):
        select_installed(duped, ())


def test_a_duplicate_in_installed_is_refused():
    with pytest.raises(ValueError, match="twice in INSTALLED"):
        select_installed(TABLE, ("cs---goods-shed", "cs---goods-shed"))


def test_a_pin_outside_the_expander_range_is_refused():
    bad = ({"sensor_id": "cs---x", "expander": 0x20, "pin": 16},)

    with pytest.raises(ValueError, match="out of range"):
        select_installed(bad, ())


def test_a_placeholder_id_in_the_table_is_refused():
    bad = ({"sensor_id": "<tbd>", "expander": 0x20, "pin": 0},)

    with pytest.raises(ValueError):
        select_installed(bad, ())


def test_a_malformed_entry_is_refused():
    with pytest.raises(ValueError, match="needs sensor_id"):
        select_installed(({"sensor_id": "cs---x"},), ())


# --- polarity ---------------------------------------------------------------

def test_active_high_reads_1_as_occupied():
    """Westgate Hollow, measured on the bench."""
    assert is_occupied(1, active_low=False) is True
    assert is_occupied(0, active_low=False) is False


def test_active_low_inverts():
    assert is_occupied(0, active_low=True) is True
    assert is_occupied(1, active_low=True) is False
