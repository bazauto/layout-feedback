"""Host stand-ins for the MicroPython runtime.

The device modules import `machine` and `utime`, neither of which exists on CPython.
`conftest.py` installs the stubs below into `sys.modules` before any test imports a
device module, which is what lets the whole host suite run with no board attached.

Two things here are deliberately more than stubs:

- `FakeClock` drives time *and* timers. `InputPinMonitor` debounces with a one-shot
  `Timer`, so a stub that only returned a number would leave the debounce path
  untestable. Advancing the clock fires whatever became due, which means a debounce
  test reads as "advance 30ms, assert" rather than as a real sleep.
- `FakeUART` records what was written as well as replaying what was queued. Most of
  what matters about the ESP-AT transport is *which command it sent*, not just what
  it did with the reply.
"""


class FakeClock:
    """Monotonic millisecond clock with a queue of pending one-shot timers."""

    def __init__(self, start_ms=0):
        self._now = start_ms
        self._pending = []  # (deadline_ms, timer)

    # -- reading -------------------------------------------------------

    def ticks_ms(self):
        return self._now

    # -- driving -------------------------------------------------------

    def advance(self, ms):
        """Move time forward, firing any timer that falls due on the way.

        Timers are fired in deadline order and re-checked after each callback, so a
        callback that arms another timer inside the same window still behaves the way
        it would on the device.
        """
        target = self._now + ms
        while True:
            due = [entry for entry in self._pending if entry[0] <= target]
            if not due:
                break
            due.sort(key=lambda entry: entry[0])
            deadline, timer = due[0]
            self._pending.remove((deadline, timer))
            self._now = deadline
            timer._fire()
        self._now = target

    # -- timer bookkeeping, used by FakeTimer --------------------------

    def _arm(self, timer, period_ms):
        self._cancel(timer)
        self._pending.append((self._now + period_ms, timer))

    def _cancel(self, timer):
        self._pending = [entry for entry in self._pending if entry[1] is not timer]

    @property
    def pending_timers(self):
        return len(self._pending)


# The clock every fake shares. `conftest.py` resets it between tests.
clock = FakeClock()


def ticks_ms():
    return clock.ticks_ms()


def ticks_diff(a, b):
    """Plain subtraction.

    MicroPython's real `ticks_diff` handles wraparound of a bounded counter. The fake
    clock never wraps, so plain subtraction is exact here — and a test that needs to
    prove wraparound behaviour should say so explicitly rather than relying on this.
    """
    return a - b


def sleep_ms(ms):
    clock.advance(ms)


def sleep(seconds):
    clock.advance(int(seconds * 1000))


class FakePin:
    """`machine.Pin`. Reads a value the test sets, and records IRQ registration."""

    IN = 0
    OUT = 1
    PULL_UP = 2
    PULL_DOWN = 3
    IRQ_RISING = 4
    IRQ_FALLING = 8

    def __init__(self, pin_id, mode=IN, pull=None, value=1):
        self.pin_id = pin_id
        self.mode = mode
        self.pull = pull
        self._value = value
        self.irq_handler = None
        self.irq_trigger = None

    def value(self, level=None):
        if level is None:
            return self._value
        self._value = 1 if level else 0
        return None

    def on(self):
        self._value = 1

    def off(self):
        self._value = 0

    def irq(self, trigger=None, handler=None):
        self.irq_trigger = trigger
        self.irq_handler = handler

    # -- test driving --------------------------------------------------

    def set(self, level):
        """Change the level and fire the registered IRQ, as the hardware would."""
        new = 1 if level else 0
        if new == self._value:
            return
        self._value = new
        if self.irq_handler is not None:
            self.irq_handler(self)


class FakeTimer:
    """`machine.Timer`, one-shot only — the only mode the device code uses."""

    ONE_SHOT = 0
    PERIODIC = 1

    def __init__(self, timer_id=-1):
        self.timer_id = timer_id
        self._callback = None
        self.fire_count = 0

    def init(self, mode=ONE_SHOT, period=0, callback=None):
        if mode != self.ONE_SHOT:
            raise NotImplementedError("only ONE_SHOT is modelled; add it when needed")
        self._callback = callback
        clock._arm(self, period)

    def deinit(self):
        clock._cancel(self)
        self._callback = None

    def _fire(self):
        self.fire_count += 1
        callback = self._callback
        self._callback = None
        if callback is not None:
            callback(self)


class FakeI2C:
    """`machine.I2C` over a dict of {address: {register: value}}.

    Registers default to 0. Reads from an address not in `devices` raise `OSError`,
    which is what the drivers expect from absent hardware.
    """

    def __init__(self, bus_id=0, sda=None, scl=None, freq=400_000, devices=None):
        self.bus_id = bus_id
        self.sda = sda
        self.scl = scl
        self.freq = freq
        self.devices = devices if devices is not None else {}
        self.writes = []  # (address, register, bytes) — register is None for raw writes

    def scan(self):
        return sorted(self.devices)

    def _registers(self, address):
        if address not in self.devices:
            raise OSError("no device at 0x%02X" % address)
        return self.devices[address]

    def readfrom_mem(self, address, register, length):
        registers = self._registers(address)
        return bytes(registers.get(register + offset, 0) for offset in range(length))

    def writeto_mem(self, address, register, data):
        registers = self._registers(address)
        for offset, byte in enumerate(bytes(data)):
            registers[register + offset] = byte
        self.writes.append((address, register, bytes(data)))

    def readfrom(self, address, length):
        registers = self._registers(address)
        return bytes(registers.get(offset, 0) for offset in range(length))

    def writeto(self, address, data):
        self._registers(address)
        self.writes.append((address, None, bytes(data)))


class FakeUART:
    """`machine.UART` with an inspectable transmit log and a scriptable receive queue."""

    def __init__(self, uart_id=1, baudrate=9600, tx=None, rx=None, **kwargs):
        self.uart_id = uart_id
        self.baudrate = baudrate
        self.tx = tx
        self.rx = rx
        self.written = bytearray()
        self._rx = bytearray()
        self.deinit_count = 0

    # -- device-facing API ---------------------------------------------

    def write(self, data):
        payload = data.encode("ascii") if isinstance(data, str) else bytes(data)
        self.written.extend(payload)
        return len(payload)

    def any(self):
        return len(self._rx)

    def read(self, length=None):
        if not self._rx:
            return None
        if length is None:
            length = len(self._rx)
        chunk = bytes(self._rx[:length])
        del self._rx[:length]
        return chunk

    def deinit(self):
        self.deinit_count += 1

    # -- test driving --------------------------------------------------

    def feed(self, data):
        """Queue bytes as though the modem had sent them."""
        self._rx.extend(data.encode("ascii") if isinstance(data, str) else bytes(data))

    def feed_line(self, line):
        """Queue one CRLF-terminated line, which is how the modem actually speaks."""
        self.feed(line + "\r\n")

    @property
    def sent_lines(self):
        """Everything written, split on CRLF, with the trailing empty entry dropped."""
        text = bytes(self.written).decode("ascii", "replace")
        return [line for line in text.split("\r\n") if line]

    @property
    def last_sent(self):
        lines = self.sent_lines
        return lines[-1] if lines else None

    def clear_written(self):
        self.written = bytearray()
