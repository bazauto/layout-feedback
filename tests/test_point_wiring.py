"""Decoding a point's contact pair, and refusing an allocation that would lie.

The hazards these guard are in `docs/point-position-feedback.md`. The one to keep in
mind: a point is commanded and read by two devices that know nothing about each other,
so a mis-allocation here means the orchestrator commands one motor and reads another —
with both ends looking healthy.
"""

import pytest

from layout_mqtt import POSITION_NORMAL, POSITION_REVERSE, POSITION_UNKNOWN
from point_wiring import (
    assert_no_pin_overlap, decode_position, is_closed, select_installed_points,
)

P1 = "88d5527d-c7db-40ab-b27b-532503487c32"
P2 = "acc2150b-ffa1-4733-b2ff-1e4d2147dfe8"

POINTS = (
    {"point_id": P1, "expander": 0x22, "normal_pin": 0, "reverse_pin": 1},
    {"point_id": P2, "expander": 0x22, "normal_pin": 2, "reverse_pin": 3},
)


# --- decoding ---------------------------------------------------------------

def test_one_contact_closed_gives_a_corroborated_position():
    assert decode_position(True, False) == (POSITION_NORMAL, False)
    assert decode_position(False, True) == (POSITION_REVERSE, False)


def test_both_open_is_unknown_and_not_a_fault():
    """A break-before-make changeover passes through both-open on every throw.

    The honest sequence is normal -> unknown -> reverse. Suppressing it to keep the log
    tidy would hide exactly the transition the backend is waiting for.
    """
    assert decode_position(False, False) == (POSITION_UNKNOWN, False)


def test_both_closed_is_unknown_and_flagged_as_cross_wiring():
    """Impossible on an SPDT, so the wiring is not what config.py says it is."""
    position, cross_wired = decode_position(True, True)

    assert position == POSITION_UNKNOWN, "the safe answer, not a guess between the two"
    assert cross_wired is True


def test_a_position_is_never_inferred_from_a_single_absence():
    """The whole argument for two inputs: absence is not evidence of the other throw."""
    assert decode_position(False, False)[0] != POSITION_REVERSE
    assert decode_position(False, False)[0] != POSITION_NORMAL


def test_a_closed_contact_reads_low():
    """S2-C is wired to 0 V, so a closed throw pulls its input down."""
    assert is_closed(0, active_low=True) is True
    assert is_closed(1, active_low=True) is False
    assert is_closed(1, active_low=False) is True


# --- the allocation ---------------------------------------------------------

def test_only_installed_points_are_selected():
    assert select_installed_points(POINTS, ()) == []
    assert select_installed_points(POINTS, (P2,)) == [POINTS[1]]


def test_installed_order_is_preserved():
    assert [e["point_id"] for e in select_installed_points(POINTS, (P2, P1))] == [P2, P1]


def test_a_point_cannot_read_one_pin_twice():
    """That is the one-input design wearing the two-input design's clothes.

    It would corroborate itself and always agree, so `unknown` could never be observed.
    """
    bad = ({"point_id": P1, "expander": 0x22, "normal_pin": 4, "reverse_pin": 4},)

    with pytest.raises(ValueError, match="two separate contacts"):
        select_installed_points(bad, (P1,))


def test_two_points_cannot_share_a_pin():
    bad = (
        {"point_id": P1, "expander": 0x22, "normal_pin": 0, "reverse_pin": 1},
        {"point_id": P2, "expander": 0x22, "normal_pin": 1, "reverse_pin": 2},
    )

    with pytest.raises(ValueError, match="both on expander"):
        select_installed_points(bad, ())


def test_a_duplicate_point_id_is_refused():
    bad = (POINTS[0], POINTS[0])

    with pytest.raises(ValueError, match="appears twice"):
        select_installed_points(bad, ())


def test_an_installed_point_missing_from_the_table_is_refused():
    with pytest.raises(ValueError, match="not in POINTS"):
        select_installed_points(POINTS, ("not-a-configured-point",))


def test_a_pin_out_of_range_is_refused():
    bad = ({"point_id": P1, "expander": 0x22, "normal_pin": 0, "reverse_pin": 16},)

    with pytest.raises(ValueError, match="out of range"):
        select_installed_points(bad, ())


def test_a_placeholder_point_id_is_refused():
    bad = ({"point_id": "<point-id>", "expander": 0x22,
            "normal_pin": 0, "reverse_pin": 1},)

    with pytest.raises(ValueError, match="placeholder"):
        select_installed_points(bad, ())


def test_a_malformed_entry_names_what_it_needs():
    with pytest.raises(ValueError, match="normal_pin"):
        select_installed_points(({"point_id": P1, "expander": 0x22},), ())


# --- the two tables against each other --------------------------------------

def test_a_sensor_and_a_point_cannot_claim_the_same_pin():
    """Validated separately, so nothing else would notice both configuring one pin."""
    sensors = [{"sensor_id": "cs---goods-shed", "expander": 0x22, "pin": 3}]

    with pytest.raises(ValueError, match="both on expander"):
        assert_no_pin_overlap(sensors, list(POINTS))


def test_the_usual_case_has_no_overlap():
    sensors = [{"sensor_id": "cs---goods-shed", "expander": 0x20, "pin": 8}]

    assert_no_pin_overlap(sensors, list(POINTS)) is None
