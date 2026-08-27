"""Startup supervision: the node retries, reports, and eventually resets.

The behaviour being protected here is that a node which cannot start never falls silent.
It used to: `main()` was called bare, the exception propagated out of `main.py`, and the
board sat at a REPL with its LED off until someone power-cycled it. Nothing in the
system pointed at the node — the orchestrator only saw a sensor go stale.
"""

import pytest

from mqtt_at import BrokerUnreachable, ModemNotResponding, NetworkNotReady
from node_startup import (
    CODE_BROKER, CODE_MODEM, CODE_NETWORK, CODE_RUNTIME, CODE_SENSORS, CODE_UNKNOWN,
    RETRY_BACKOFF_MS, SensorWiringError, StartupFailed, code_for_error, start_supervised,
)


class Recorder:
    """Stands in for the LED and the reset line."""

    def __init__(self):
        self.failures = []   # (code, attempt_number, delay_ms)
        self.resets = 0

    def on_failure(self, exc, code, attempt, delay_ms):
        self.failures.append((code, attempt, delay_ms))

    def reset(self):
        self.resets += 1

    @property
    def codes(self):
        return [code for code, _attempt, _delay in self.failures]


def always_failing(exc):
    def attempt():
        raise exc
    return attempt


# --- the codes --------------------------------------------------------------

@pytest.mark.parametrize("exc, expected", [
    (SensorWiringError("expander 0x21 not on the bus"), CODE_SENSORS),
    (ModemNotResponding("AT+RST failed"), CODE_MODEM),
    (NetworkNotReady("no IP"), CODE_NETWORK),
    (BrokerUnreachable("connect timed out"), CODE_BROKER),
])
def test_each_stage_gets_its_own_code(exc, expected):
    assert code_for_error(exc) == expected


def test_an_unrecognised_failure_still_gets_a_code():
    """Silence is the failure being fixed. Anything unclassified still flashes."""
    assert code_for_error(ValueError("something else entirely")) == CODE_UNKNOWN


def test_the_codes_are_all_distinct_and_countable():
    codes = [CODE_SENSORS, CODE_MODEM, CODE_NETWORK, CODE_BROKER, CODE_RUNTIME,
             CODE_UNKNOWN]
    assert len(set(codes)) == len(codes)
    assert min(codes) >= 2, "a single flash is too easily confused with a boot blink"


# --- retrying ---------------------------------------------------------------

def test_a_transient_failure_is_retried_and_the_node_comes_up():
    """The fault this was written for: the modem is asleep, then it is not."""
    calls = {"n": 0}

    def attempt():
        calls["n"] += 1
        if calls["n"] < 3:
            raise ModemNotResponding("modem did not answer AT")
        return "running"

    recorder = Recorder()

    assert start_supervised(attempt, recorder.on_failure, recorder.reset) == "running"
    assert calls["n"] == 3
    assert recorder.codes == [CODE_MODEM, CODE_MODEM]
    assert recorder.resets == 0, "a startup that succeeded must not reset the board"


def test_a_successful_first_attempt_reports_nothing():
    recorder = Recorder()

    assert start_supervised(lambda: "running", recorder.on_failure, recorder.reset) == "running"
    assert recorder.failures == []
    assert recorder.resets == 0


def test_every_failure_is_reported_before_the_next_attempt():
    """The reporting callback is also the wait — the LED flashes for the backoff."""
    recorder = Recorder()

    with pytest.raises(StartupFailed):
        start_supervised(always_failing(BrokerUnreachable("no broker")),
                         recorder.on_failure, recorder.reset)

    assert [delay for _code, _attempt, delay in recorder.failures] == list(RETRY_BACKOFF_MS)
    assert [attempt for _code, attempt, _delay in recorder.failures] == [1, 2, 3]
    assert all(delay > 0 for _c, _a, delay in recorder.failures), \
        "a zero wait would flash a code nobody could see"


# --- giving up --------------------------------------------------------------

def test_the_board_is_reset_when_every_attempt_fails():
    recorder = Recorder()

    with pytest.raises(StartupFailed):
        start_supervised(always_failing(NetworkNotReady("no IP")),
                         recorder.on_failure, recorder.reset)

    assert recorder.resets == 1
    assert len(recorder.failures) == len(RETRY_BACKOFF_MS)


def test_a_failed_startup_never_returns_as_though_it_worked():
    """On the device `reset()` never returns. On the host it does, and a bare return
    here would hand `main()` a None it would then try to run."""
    recorder = Recorder()

    with pytest.raises(StartupFailed):
        start_supervised(always_failing(ValueError("unclassified")),
                         recorder.on_failure, recorder.reset)

    assert recorder.codes == [CODE_UNKNOWN] * len(RETRY_BACKOFF_MS)


def test_a_wiring_fault_is_retried_too_rather_than_halting():
    """An expander missing at boot is usually a loose ribbon, not a permanent fact."""
    recorder = Recorder()

    with pytest.raises(StartupFailed):
        start_supervised(always_failing(SensorWiringError("expander 0x21 missing")),
                         recorder.on_failure, recorder.reset)

    assert recorder.codes == [CODE_SENSORS] * len(RETRY_BACKOFF_MS)
    assert recorder.resets == 1
