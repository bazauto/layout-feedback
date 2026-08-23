"""Broker link state: failures that are visible, and a connection that comes back.

Before this, a node whose broker had gone away carried on publishing into nothing and
reporting success. That is the failure mode where the layout believes it has sensor
coverage it does not have — worse than the node crashing, which at least shows up.
"""

import mqtt_at
from mqtt_at import MQTTATClient


def make_client(uart, **kwargs):
    return MQTTATClient("172.18.10.240", 1883, uart=uart, **kwargs)


# --- failures are returned, not swallowed ----------------------------------

def test_publish_reports_failure(uart):
    client = make_client(uart)
    uart.feed_line("ERROR")

    assert client.publish("t", "m") is False


def test_publish_reports_success(uart):
    client = make_client(uart)
    uart.feed_line("OK")

    assert client.publish("t", "m") is True


def test_subscribe_reports_failure(uart):
    client = make_client(uart)
    uart.feed_line("ERROR")

    assert client.subscribe("t/#") is False
    assert client._subscriptions == [], "a failed subscription must not be remembered"


def test_a_failed_publish_arms_a_reconnect(uart):
    """The modem refusing a publish is often the first sign the link has gone.

    It is not always accompanied by a +MQTTDISCONNECTED, so waiting for one would leave
    the node publishing into a dead link indefinitely.
    """
    client = make_client(uart)
    uart.feed_line("ERROR")

    client.publish("t", "m")

    assert client._next_reconnect_ms is not None


# --- link state comes from the modem, not from command results -------------

def test_connected_notification_sets_the_link_up(uart):
    client = make_client(uart)
    uart.feed_line('+MQTTCONNECTED:0,1,"172.18.10.240","1883","",1')

    client.poll()

    assert client.connected is True


def test_disconnected_notification_sets_the_link_down_and_arms_a_retry(uart, clock):
    client = make_client(uart)
    client.connected = True
    uart.feed_line("+MQTTDISCONNECTED:0")

    client.poll()

    assert client.connected is False
    assert client._next_reconnect_ms == clock.ticks_ms() + client.reconnect_backoff_ms


# --- reconnect --------------------------------------------------------------

def test_no_reconnect_before_the_backoff_elapses(uart, clock):
    client = make_client(uart)
    uart.feed_line("+MQTTDISCONNECTED:0")
    client.poll()
    uart.clear_written()

    clock.advance(client.reconnect_backoff_ms - 100)
    client.poll()

    assert uart.sent_lines == [], "reconnected early"


def test_reconnect_after_the_backoff_and_resubscribe(uart, clock):
    client = make_client(uart)

    uart.feed_line("OK")
    client.subscribe("layout/x/point/+/command")
    assert client._subscriptions == [("layout/x/point/+/command", 0)]

    uart.feed_line("+MQTTDISCONNECTED:0")
    client.poll()
    uart.clear_written()

    # The reconnect and the re-subscribe each wait for their own OK, so the modem has
    # to answer as they are sent rather than in advance.
    uart.auto_reply = "OK"
    clock.advance(client.reconnect_backoff_ms + 1)
    client.poll()

    sent = uart.sent_lines
    assert sent[0] == 'AT+MQTTCONN=0,"172.18.10.240",1883,1'
    assert sent[1] == 'AT+MQTTSUB=0,"layout/x/point/+/command",0', (
        "subscriptions do not survive a broker disconnect and must be restored")


def test_a_failed_reconnect_retries_rather_than_giving_up(uart, clock):
    client = make_client(uart)
    uart.feed_line("+MQTTDISCONNECTED:0")
    client.poll()

    uart.clear_written()
    uart.auto_reply = "ERROR"
    clock.advance(client.reconnect_backoff_ms + 1)
    client.poll()
    assert uart.sent_lines[0].startswith("AT+MQTTCONN")

    uart.clear_written()
    uart.auto_reply = "OK"
    clock.advance(client.reconnect_backoff_ms + 1)
    client.poll()
    assert uart.sent_lines[0].startswith("AT+MQTTCONN"), "gave up after one failure"


def test_reconnect_does_not_re_enter_the_modem_mid_command(uart, clock):
    """`send_at_and_wait()` polls while it waits, so an unguarded reconnect would
    interleave a second AT command with the one already in flight."""
    client = make_client(uart)
    uart.feed_line("+MQTTDISCONNECTED:0")
    client.poll()
    clock.advance(client.reconnect_backoff_ms + 1)

    uart.clear_written()
    client.send_at_and_wait("AT+SOMETHING", timeout_ms=200)

    assert uart.sent_lines == ["AT+SOMETHING"], (
        "a reconnect was issued from inside another command")


# --- diagnostics ------------------------------------------------------------

def test_recent_lines_survive_the_next_command(uart):
    """They used to be cleared on every send, which threw away the very lines a
    failure dump was about to print and that wait_for_line() was scanning for."""
    client = make_client(uart)
    uart.feed_line("+ETH_GOT_IP:172.18.10.98")
    client.poll()

    uart.feed_line("OK")
    client.send_at_and_wait("AT")

    assert any("+ETH_GOT_IP" in line for line in client._recent_lines)


def test_lines_since_command_scopes_the_dump_to_this_command(uart):
    client = make_client(uart)
    uart.feed_line("older noise")
    client.poll()

    uart.feed_line("ERROR")
    client.send_at_and_wait("AT+THING")

    scoped = client.lines_since_command()
    assert "older noise" not in scoped
    assert "ERROR" in scoped
