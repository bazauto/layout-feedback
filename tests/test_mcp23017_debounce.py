"""MCP23017 input debouncing.

A chattering IR beam produced 2-3 contradictory readings per second on the bench. The
old implementation compared against the last *accepted* change, which capped the rate of
reported changes without ever requiring the signal to settle — so a bouncing input was
faithfully republished as a stream of occupied/clear flips.
"""

import pytest
from machine import I2C

from mcp23017_io import MCP23017Expander

ADDRESS = 0x20
GPIO_A = 0x12
GPIO_B = 0x13
DEBOUNCE_MS = 200


@pytest.fixture
def bus():
    # All 16 inputs high, as the real board sits with its pull-ups on.
    return I2C(0, devices={ADDRESS: {GPIO_A: 0xFF, GPIO_B: 0xFF}})


def set_pin8(bus, level):
    """Pin 8 is GPB0 — the bit the Goods Shed sensors are actually wired to."""
    port = bus.devices[ADDRESS][GPIO_B]
    bus.devices[ADDRESS][GPIO_B] = (port | 0x01) if level else (port & ~0x01)


@pytest.fixture
def pin(bus):
    expander = MCP23017Expander(bus, address=ADDRESS)
    pin = expander.create_pin(8, mode="input", debounce_ms=DEBOUNCE_MS)
    pin.setup()
    return pin


def poll_for(pin, clock, duration_ms, step_ms=50):
    """Poll the way the node's loop does, returning how many changes were reported."""
    changes = 0
    elapsed = 0
    while elapsed < duration_ms:
        if pin.update_state():
            changes += 1
        clock.advance(step_ms)
        elapsed += step_ms
    return changes


def test_a_settled_change_is_reported_once(bus, pin, clock):
    assert pin.state == 1

    set_pin8(bus, 0)
    changes = poll_for(pin, clock, DEBOUNCE_MS * 3)

    assert changes == 1
    assert pin.state == 0


def test_a_change_is_not_reported_before_the_window_elapses(bus, pin, clock):
    set_pin8(bus, 0)

    assert poll_for(pin, clock, DEBOUNCE_MS - 50) == 0
    assert pin.state == 1, "reported before the level had settled"


def test_a_bounce_that_returns_is_never_reported(bus, pin, clock):
    """The level dips and comes back inside the window — nothing happened."""
    set_pin8(bus, 0)
    poll_for(pin, clock, 100)
    set_pin8(bus, 1)
    changes = poll_for(pin, clock, DEBOUNCE_MS * 3)

    assert changes == 0
    assert pin.state == 1


def test_a_continuously_chattering_input_reports_nothing(bus, pin, clock):
    """The bench failure. The old code reported a change every debounce_ms forever;
    a signal that will not sit still is not evidence of anything."""
    changes = 0
    for i in range(200):
        set_pin8(bus, i % 2)
        if pin.update_state():
            changes += 1
        clock.advance(50)

    assert changes == 0, "chatter was republished as real state changes"
    assert pin.state == 1


def test_chatter_that_finally_settles_is_reported_once(bus, pin, clock):
    for i in range(20):
        set_pin8(bus, i % 2)
        pin.update_state()
        clock.advance(50)

    set_pin8(bus, 0)
    changes = poll_for(pin, clock, DEBOUNCE_MS * 3)

    assert changes == 1
    assert pin.state == 0


def test_both_directions_debounce(bus, pin, clock):
    set_pin8(bus, 0)
    assert poll_for(pin, clock, DEBOUNCE_MS * 2) == 1

    set_pin8(bus, 1)
    assert poll_for(pin, clock, DEBOUNCE_MS * 2) == 1
    assert pin.state == 1


def test_zero_debounce_reports_immediately(bus, clock):
    expander = MCP23017Expander(bus, address=ADDRESS)
    pin = expander.create_pin(8, mode="input", debounce_ms=0)
    pin.setup()

    set_pin8(bus, 0)

    assert pin.update_state() is True
    assert pin.state == 0


def test_pin_8_is_port_b_bit_0(bus, pin, clock):
    """Guards the bank split: pins 0-7 are GPA0-7, 8-15 are GPB0-7. Getting this wrong
    would read a different sensor's pin entirely."""
    bus.devices[ADDRESS][GPIO_A] = 0x00  # every port A pin low
    changes = poll_for(pin, clock, DEBOUNCE_MS * 3)

    assert changes == 0, "pin 8 followed port A"
    assert pin.state == 1
