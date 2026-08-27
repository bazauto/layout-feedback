"""`setup()` against a modem that is still booting.

The node used to die outright on a power cycle, roughly whenever the Ethernet link took
the slow path. The Pico reaches its first AT command about 300 ms after power-on; the
modem needs 1.2-3.5 s to reach `ready`, and a command sent into that window is swallowed
whole — no echo, no reply. `AT+RST` then timed out against its 2 s default and
`main.py` exited to a REPL nobody was watching.

Measured on the bench 2026-08-27; the modem's boot spread is Ethernet negotiation.
"""

import pytest

from mqtt_at import (
    BrokerUnreachable, MQTTATClient, ModemNotResponding, NetworkNotReady,
)

CONNECTED = '+MQTTCONNECTED:0,1,"172.18.10.240","1883","",1'


def make_client(uart, **kwargs):
    return MQTTATClient("172.18.10.240", 1883, uart=uart, **kwargs)


def booting_modem(silent_probes=0, got_ip=True, broker="ok"):
    """A modem that ignores everything until it has finished booting.

    `silent_probes` is how many bare `AT`s go unanswered before it wakes up. Silence is
    the point: a booting ESP does not answer with ERROR, it does not answer at all.
    """
    state = {"probes": 0}

    def reply(line):
        if line == "AT":
            state["probes"] += 1
            return None if state["probes"] <= silent_probes else "OK"
        if state["probes"] <= silent_probes:
            return None
        if line == "AT+RST":
            return "OK\r\n+ETH_GOT_IP:172.18.10.98" if got_ip else "OK"
        if line.startswith("AT+MQTTCONN="):
            if broker == "refused":
                return "ERROR"
            return "OK\r\n" + CONNECTED if broker == "ok" else "OK"
        return "OK"

    return reply


# --- waiting for the modem --------------------------------------------------

def test_the_modem_is_asked_whether_it_is_awake_before_it_is_given_an_order(uart):
    """The regression. Four probes of silence is ~2 s — the old deadline for AT+RST."""
    client = make_client(uart)
    uart.auto_reply = booting_modem(silent_probes=4)

    client.setup()

    sent = uart.sent_lines
    assert sent[:5] == ["AT"] * 5, "it must probe until answered, not assume"
    assert sent[5] == "AT+RST", "the reset only goes out once the modem has answered"
    assert client.connected is True


def test_startup_survives_a_modem_slower_than_the_old_two_second_deadline(uart, clock):
    client = make_client(uart)
    uart.auto_reply = booting_modem(silent_probes=6)  # ~3 s, the measured worst case

    client.setup()

    assert clock.ticks_ms() > 2000, \
        "this must be the case the old 2 s AT+RST timeout would have killed"


def test_a_modem_that_never_wakes_says_so_and_never_sends_a_reset(uart):
    client = make_client(uart)
    uart.auto_reply = booting_modem(silent_probes=10_000)

    with pytest.raises(ModemNotResponding):
        client.setup()

    assert "AT+RST" not in uart.sent_lines, \
        "commanding a modem that has not answered is what produced the silent death"


def test_wait_for_modem_returns_as_soon_as_it_answers(uart):
    client = make_client(uart)
    uart.auto_reply = booting_modem(silent_probes=2)

    assert client.wait_for_modem(timeout_ms=10_000, probe_interval_ms=500) is True
    assert uart.sent_lines == ["AT"] * 3


def test_wait_for_modem_gives_up_rather_than_blocking_forever(uart, clock):
    client = make_client(uart)
    uart.auto_reply = None  # total silence

    assert client.wait_for_modem(timeout_ms=2000, probe_interval_ms=500) is False
    assert clock.ticks_ms() >= 2000


def test_boot_chatter_does_not_stop_the_modem_being_detected(uart):
    client = make_client(uart)

    def reply(line):
        uart.feed_line("ets Jun  8 2016 00:22:57 rst cause:2")  # ESP boot log
        return "OK"

    uart.auto_reply = reply

    assert client.wait_for_modem(timeout_ms=1000) is True
    assert client._rx_buffer == b"", \
        "stale bytes must not be left to prefix the reset's reply"


# --- the stage a failure came from ------------------------------------------

def test_a_network_that_never_reports_an_ip_is_its_own_failure(uart):
    client = make_client(uart)
    uart.auto_reply = booting_modem(got_ip=False)

    with pytest.raises(NetworkNotReady):
        client.setup()


def test_an_ip_arriving_with_the_resets_own_ok_is_not_missed(uart, clock):
    """A fast link puts both in the same read. Waiting for it again would time out."""
    client = make_client(uart)
    uart.auto_reply = booting_modem()

    client.setup()

    assert clock.ticks_ms() < 30_000, "it must not have sat out the 30 s IP wait"


def test_a_broker_that_refuses_the_connection_is_its_own_failure(uart):
    client = make_client(uart)
    uart.auto_reply = booting_modem(broker="refused")

    with pytest.raises(BrokerUnreachable):
        client.setup()


def test_a_modem_that_accepts_the_connect_but_never_confirms_it_is_a_broker_failure(uart):
    """A True from the modem means it took the command, not that the broker answered."""
    client = make_client(uart)
    uart.auto_reply = booting_modem(broker="silent")

    with pytest.raises(BrokerUnreachable):
        client.setup()


def test_the_startup_failures_are_still_runtime_errors(uart):
    """They subclass RuntimeError, which is what callers already catch."""
    for failure in (ModemNotResponding, NetworkNotReady, BrokerUnreachable):
        assert issubclass(failure, RuntimeError)
