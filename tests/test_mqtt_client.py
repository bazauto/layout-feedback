"""`MQTTATClient` driven end to end over a fake UART.

The existing parser tests cover the static line parser. These cover the client around
it — what it puts on the wire, and what it does with what comes back — which is the
part the contract work in #2 is about to change.
"""

from mqtt_at import MQTTATClient


def make_client(uart):
    """A client bound to a fake UART, skipping `setup()`.

    `setup()` drives the modem's reset-and-connect handshake, which is entangled with
    the `_recent_lines` diagnostics bug (#2) and is not what these tests are about.
    Passing an existing UART is a supported constructor path, not a test-only hook.
    """
    return MQTTATClient("172.18.10.240", 1883, uart=uart)


def test_publish_sends_the_expected_at_command(uart):
    client = make_client(uart)
    uart.feed_line("OK")

    client.publish("track/test", "hello")

    assert uart.last_sent == 'AT+MQTTPUB=0,"track/test","hello",0,0'


def test_publish_escapes_quotes_and_backslashes(uart):
    """An unescaped quote would terminate the AT argument early.

    This is why JSON payloads cannot simply be dropped into the existing publish path
    — see #2, where the escaping is confirmed against the real modem before anything
    depends on it.
    """
    client = make_client(uart)
    uart.feed_line("OK")

    client.publish("track/test", '{"state":"occupied"}')

    assert uart.last_sent == 'AT+MQTTPUB=0,"track/test","{\\"state\\":\\"occupied\\"}",0,0'


def test_subscribe_sends_the_expected_at_command(uart):
    client = make_client(uart)
    uart.feed_line("OK")

    client.subscribe("track/#")

    assert uart.last_sent == 'AT+MQTTSUB=0,"track/#",0'


def test_an_incoming_message_reaches_a_registered_handler(uart):
    client = make_client(uart)
    received = []
    client.register_topic_handler("track/", lambda topic, payload: received.append((topic, payload)))

    uart.feed_line('+MQTTSUBRECV:0,"track/sensor/a",6,ACTIVE')
    client.poll()

    assert received == [("track/sensor/a", "ACTIVE")]


def test_a_handler_only_sees_topics_matching_its_prefix(uart):
    client = make_client(uart)
    received = []
    client.register_topic_handler("track/", lambda topic, payload: received.append(topic))

    uart.feed_line('+MQTTSUBRECV:0,"other/thing",2,hi')
    client.poll()

    assert received == []


def test_send_at_and_wait_returns_true_on_ok(uart):
    client = make_client(uart)
    uart.feed_line("OK")

    assert client.send_at_and_wait("AT") is True


def test_send_at_and_wait_returns_false_on_error(uart):
    client = make_client(uart)
    uart.feed_line("ERROR")

    assert client.send_at_and_wait("AT") is False


def test_send_at_and_wait_times_out_without_burning_real_time(uart, clock):
    """Proves the clock patch works: a 2 s timeout costs no wall-clock time."""
    client = make_client(uart)
    started = clock.ticks_ms()

    assert client.send_at_and_wait("AT", timeout_ms=2000) is False
    assert clock.ticks_ms() - started >= 2000, "the timeout must actually elapse on the fake clock"


def test_close_deinits_only_a_uart_the_client_owns(uart):
    """A UART passed in belongs to the caller; closing must not tear it down."""
    client = make_client(uart)

    client.close()

    assert uart.deinit_count == 0
    assert client.uart is uart
