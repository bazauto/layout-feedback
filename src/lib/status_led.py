"""The onboard LED as the node's only diagnostic channel.

A node on the layout has no console. The board is behind the baseboard, the USB lead is
not plugged into anything, and the first thing anyone notices about a node that failed
to start is that its LED never came on. So the LED has to carry more than "powered":

| LED                      | Meaning                                            |
|--------------------------|----------------------------------------------------|
| Solid on                 | Running: sensors configured, broker link up        |
| Off                      | No power, or a hard crash that took the board out  |
| N flashes, pause, repeat | Startup failed at stage N; retrying, then resetting|

The codes themselves live in `node_startup.py`, next to the policy that chooses them —
this module only knows how to make a countable pattern out of a number.

**No `machine` import here.** The pin is passed in, so the whole pattern is provable on
the host rather than by standing in front of the baseboard counting.
"""

from utime import sleep_ms as _sleep_ms, ticks_diff as _ticks_diff, ticks_ms as _ticks_ms


# Slow enough to count from across a room, and slow enough that a phone camera catches
# it. The gap has to be clearly longer than the off time between flashes or a 2 reads as
# a 4 — the whole value of a code is that it is unambiguous at a glance.
FLASH_ON_MS = 200
FLASH_OFF_MS = 250
GAP_MS = 1400


def blink_plan(code, on_ms=FLASH_ON_MS, off_ms=FLASH_OFF_MS, gap_ms=GAP_MS):
    """One cycle of `code` flashes as a list of `(level, duration_ms)` steps.

    The last flash is followed by the long gap rather than the short one, which is what
    separates one cycle from the next.
    """
    if code < 1:
        raise ValueError("a flash code must be at least 1, got %r" % (code,))

    plan = []
    for index in range(code):
        last = index == code - 1
        plan.append((1, on_ms))
        plan.append((0, gap_ms if last else off_ms))
    return plan


class StatusLED:
    """Drives a `machine.Pin` (or anything with `value()`) as a status indicator."""

    def __init__(self, pin, sleep_ms=None, ticks_ms=None, ticks_diff=None):
        self._pin = pin
        self._sleep_ms = sleep_ms if sleep_ms is not None else _sleep_ms
        self._ticks_ms = ticks_ms if ticks_ms is not None else _ticks_ms
        self._ticks_diff = ticks_diff if ticks_diff is not None else _ticks_diff

    def on(self):
        self._pin.value(1)

    def off(self):
        self._pin.value(0)

    def flash(self, code, duration_ms):
        """Repeat `code` for about `duration_ms`, then go dark.

        Always completes the cycle it is in, so it can overrun `duration_ms` by up to
        one cycle. That is deliberate: a code cut off half way through cannot be
        counted, and counting it is the entire point. At least one full cycle is always
        shown, even when `duration_ms` is 0.
        """
        plan = blink_plan(code)
        start = self._ticks_ms()

        while True:
            for level, hold_ms in plan:
                self._pin.value(level)
                self._sleep_ms(hold_ms)
            if self._ticks_diff(self._ticks_ms(), start) >= duration_ms:
                break

        self.off()


__all__ = ["StatusLED", "blink_plan", "FLASH_ON_MS", "FLASH_OFF_MS", "GAP_MS"]
