"""The fakes carry enough behaviour to be worth testing themselves.

A debounce test that passes because the fake timer never fires would be worse than no
test at all, so the scaffolding proves itself here rather than in the tests that rely
on it.
"""

from machine import I2C, Pin, Timer


def test_clock_fires_a_timer_when_time_passes_its_deadline(clock):
    fired = []
    timer = Timer(-1)
    timer.init(mode=Timer.ONE_SHOT, period=25, callback=lambda t: fired.append(clock.ticks_ms()))

    clock.advance(24)
    assert fired == [], "fired early"

    clock.advance(1)
    assert fired == [25]


def test_clock_leaves_time_at_the_requested_point_after_firing(clock):
    timer = Timer(-1)
    timer.init(mode=Timer.ONE_SHOT, period=10, callback=lambda t: None)

    clock.advance(100)

    assert clock.ticks_ms() == 100, "advancing past a timer must not stop at its deadline"
    assert clock.pending_timers == 0


def test_deinit_cancels_a_pending_timer(clock):
    fired = []
    timer = Timer(-1)
    timer.init(mode=Timer.ONE_SHOT, period=10, callback=lambda t: fired.append(1))
    timer.deinit()

    clock.advance(100)

    assert fired == []


def test_a_timer_armed_from_inside_a_callback_still_fires(clock):
    """The debounce path re-arms its timer from a callback, so this must work."""
    fired = []
    second = Timer(-1)

    def rearm(_timer):
        fired.append("first")
        second.init(mode=Timer.ONE_SHOT, period=5, callback=lambda t: fired.append("second"))

    first = Timer(-1)
    first.init(mode=Timer.ONE_SHOT, period=10, callback=rearm)

    clock.advance(20)

    assert fired == ["first", "second"]


def test_pin_irq_fires_on_a_level_change():
    seen = []
    pin = Pin(16, Pin.IN, Pin.PULL_UP, value=1)
    pin.irq(trigger=Pin.IRQ_FALLING, handler=lambda p: seen.append(p.value()))

    pin.set(0)
    assert seen == [0]

    pin.set(0)
    assert seen == [0], "an unchanged level must not fire the IRQ"


def test_i2c_registers_round_trip():
    i2c = I2C(0, devices={0x20: {}})

    i2c.writeto_mem(0x20, 0x12, bytes([0xA5]))

    assert i2c.readfrom_mem(0x20, 0x12, 1) == bytes([0xA5])
    assert i2c.readfrom_mem(0x20, 0x13, 1) == bytes([0x00]), "unset registers read zero"
    assert i2c.scan() == [0x20]


def test_i2c_raises_for_an_absent_device():
    i2c = I2C(0, devices={0x20: {}})

    try:
        i2c.readfrom_mem(0x21, 0x00, 1)
    except OSError:
        return
    raise AssertionError("an absent device must raise OSError, as real hardware does")


def test_uart_records_what_was_written_and_replays_what_was_fed():
    from machine import UART

    uart = UART(1, baudrate=9600)
    uart.write("AT+RST\r\n")
    uart.feed_line("OK")

    assert uart.sent_lines == ["AT+RST"]
    assert uart.last_sent == "AT+RST"
    assert uart.any() == 4
    assert uart.read(uart.any()) == b"OK\r\n"
    assert uart.read() is None, "an empty queue reads None, as MicroPython's UART does"
