"""The LED codes, proved on the host rather than by counting flashes on a baseboard.

The onboard LED is the only thing a node tells you when it cannot start, so a code that
is miscountable is a code that lies. These tests pin the shape of the pattern.
"""

import pytest

import fakes
from status_led import FLASH_OFF_MS, FLASH_ON_MS, GAP_MS, StatusLED, blink_plan


def cycle_ms(code):
    return sum(duration for _level, duration in blink_plan(code))


def make_led(clock):
    """A LED on a fake pin, with every sleep recorded as (level held, how long)."""
    pin = fakes.FakePin("LED", value=0)
    seen = []

    def sleeping(ms):
        seen.append((pin.value(), ms))
        clock.advance(ms)

    led = StatusLED(pin, sleep_ms=sleeping, ticks_ms=clock.ticks_ms,
                    ticks_diff=fakes.ticks_diff)
    return led, pin, seen


# --- the pattern -----------------------------------------------------------

def test_the_plan_is_that_many_flashes():
    assert blink_plan(2) == [(1, FLASH_ON_MS), (0, FLASH_OFF_MS),
                             (1, FLASH_ON_MS), (0, GAP_MS)]


def test_the_cycle_ends_on_the_long_gap():
    """What separates one cycle from the next, and so a 2 from a 4."""
    for code in (2, 3, 4, 5, 6, 7):
        plan = blink_plan(code)
        assert plan[-1] == (0, GAP_MS)
        assert plan.count((1, FLASH_ON_MS)) == code
        # Only the final off is long, or the flashes run together.
        assert [d for level, d in plan if level == 0].count(GAP_MS) == 1


def test_the_gap_is_clearly_longer_than_the_space_between_flashes():
    assert GAP_MS >= FLASH_OFF_MS * 3


def test_a_code_below_one_is_refused():
    with pytest.raises(ValueError):
        blink_plan(0)


# --- driving the pin -------------------------------------------------------

def test_flash_shows_a_whole_cycle_even_for_a_zero_duration(clock):
    """A code cut off half way through cannot be counted, which is the whole point."""
    led, pin, seen = make_led(clock)

    led.flash(3, 0)

    assert seen == blink_plan(3)
    assert pin.value() == 0, "the LED must not be left lit by a failure code"


def test_flash_repeats_until_the_duration_is_up(clock):
    led, _pin, seen = make_led(clock)
    code = 2

    led.flash(code, cycle_ms(code) + 50)

    assert seen == blink_plan(code) * 2


def test_flash_leaves_the_led_off_so_running_is_unambiguous(clock):
    """Solid on means running. A code that ended lit would read as a healthy node."""
    led, pin, _seen = make_led(clock)

    led.flash(5, 100)

    assert pin.value() == 0


def test_on_and_off_drive_the_pin(clock):
    led, pin, _seen = make_led(clock)

    led.on()
    assert pin.value() == 1

    led.off()
    assert pin.value() == 0
