"""Utilities for tracking digital input pin state transitions."""

from machine import Pin, Timer
from utime import ticks_ms


class InputPinMonitor:
    """Track state changes for a single digital input pin."""

    def __init__(self, pin_id, *, pull=Pin.PULL_UP, debounce_ms=25,
                 trigger=Pin.IRQ_RISING | Pin.IRQ_FALLING, timer_id=None,
                 use_interrupts=True):
        self.pin_id = pin_id
        self.pull = pull
        self.debounce_ms = debounce_ms
        self.trigger = trigger
        self.use_interrupts = use_interrupts
        self._pin = None
        self._last_state = None
        self._last_change_ms = 0
        self._pending_change = False
        self.timer_id = -1 if timer_id is None else timer_id
        self._timer = None
        self._candidate_state = None

    def setup(self):
        """Initialise the underlying pin and capture its starting state."""
        if self._pin is not None:
            return

        self._pin = Pin(self.pin_id, Pin.IN, self.pull)
        self._last_state = self._pin.value()
        self._last_change_ms = ticks_ms()
        self._pending_change = False
        self._candidate_state = None
        if self.debounce_ms > 0:
            self._ensure_timer()
        if self.use_interrupts:
            self._pin.irq(trigger=self.trigger, handler=self._handle_irq)

    def update_state(self):
        """Return True when the pin state changes after debouncing."""
        if self._pin is None:
            raise RuntimeError("Call setup() before update_state().")

        if self._pending_change:
            self._pending_change = False
            return True

        current_state = self._pin.value()
        if current_state != self._last_state:
            if self.debounce_ms <= 0:
                self._record_change(current_state, queue_change=False)
                return True
            self._begin_debounce(current_state)
        return False

    @property
    def state(self):
        """Return the most recently observed stable state."""
        if self._pin is None:
            raise RuntimeError("Call setup() before reading state.")
        return self._last_state

    def _handle_irq(self, pin):
        current_state = pin.value()
        if current_state == self._last_state:
            return
        if self.debounce_ms <= 0:
            self._record_change(current_state)
            return
        self._begin_debounce(current_state)

    def _record_change(self, new_state, now=None, *, queue_change=True):
        if now is None:
            now = ticks_ms()
        self._last_state = new_state
        self._last_change_ms = now
        self._pending_change = queue_change
        self._candidate_state = None

    def _begin_debounce(self, candidate_state):
        if candidate_state == self._last_state:
            self._candidate_state = None
            return
        self._candidate_state = candidate_state
        self._arm_debounce_timer()

    def _arm_debounce_timer(self):
        timer = self._ensure_timer()
        timer.deinit()
        timer.init(mode=Timer.ONE_SHOT, period=self.debounce_ms,
                   callback=self._debounce_callback)

    def _debounce_callback(self, _timer):
        candidate = self._candidate_state
        if candidate is None:
            return
        if self._pin is None:
            return
        current_state = self._pin.value()
        if current_state != candidate or current_state == self._last_state:
            self._candidate_state = None
            return
        self._record_change(current_state, ticks_ms())

    def _ensure_timer(self):
        if self._timer is None:
            self._timer = Timer(self.timer_id)
        return self._timer
