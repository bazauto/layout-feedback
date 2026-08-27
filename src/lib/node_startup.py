"""Startup supervision: a node that cannot start must say so, not exit.

`main()` used to be called bare at module scope, so any exception on the way up
propagated out, `main.py` ended, and the board sat at the REPL with its LED off until
somebody power-cycled it. That is the failure this repo rates worst — the node stopped
reporting and said nothing. The orchestrator did what it should (the block degraded to
`unknown`, which the fail-safe rule treats as occupied), but nothing pointed at the node.

So startup is retried, and if it still will not come up the board is reset and tries
again from cold, forever. Each failure flashes a code on the onboard LED first, because
that is the only thing anybody can see (`status_led.py`).

## The flash codes

| Code | Stage            | What it means                                          |
|------|------------------|--------------------------------------------------------|
| 2    | Sensors          | A configured expander is not on the I2C bus            |
| 3    | Modem            | The ESP-AT modem is not answering AT commands          |
| 4    | Network          | The modem answers, but never reported an IP            |
| 5    | Broker           | Network is up, but the MQTT link could not be made     |
| 6    | Runtime          | The node came up, then the main loop crashed           |
| 7    | Unknown          | Something else — read the USB console                  |

Codes start at 2 deliberately. A single flash is too easily confused with a board that
is merely blinking on boot, and a code that gets miscounted is worse than no code.

**No `machine` import here.** The reset function is passed in, so the whole policy is
provable on the host — including that it eventually resets, which is not something you
want to discover by watching a baseboard.
"""

from mqtt_at import BrokerUnreachable, ModemNotResponding, NetworkNotReady


CODE_SENSORS = 2
CODE_MODEM = 3
CODE_NETWORK = 4
CODE_BROKER = 5
CODE_RUNTIME = 6
CODE_UNKNOWN = 7

# Three attempts, flashing for 2 s, 5 s and 10 s between them, then a hard reset — about
# 17 s of trying before the board goes round again.
#
# The first retry alone covers the fault this was written for: the modem needs at most
# 3.5 s from cold and `wait_for_modem()` now waits for it, so anything still failing on
# attempt two is a real fault rather than the power-on race.
RETRY_BACKOFF_MS = (2000, 5000, 10000)


class SensorWiringError(RuntimeError):
    """The configured wiring is not what is on the I2C bus.

    Its own type so it can be told apart from a transport failure without matching on
    message text: a missing expander flashes a different code from a missing broker,
    and those two send you to opposite ends of the layout.
    """


class StartupFailed(RuntimeError):
    """Every attempt failed and the board was reset.

    On the device `reset()` never returns, so this is only ever raised on the host —
    where it is what keeps a failed startup from looking like a successful one.
    """


def code_for_error(exc):
    """Map an exception to the LED code for the stage that raised it."""
    if isinstance(exc, SensorWiringError):
        return CODE_SENSORS
    if isinstance(exc, ModemNotResponding):
        return CODE_MODEM
    if isinstance(exc, NetworkNotReady):
        return CODE_NETWORK
    if isinstance(exc, BrokerUnreachable):
        return CODE_BROKER
    return CODE_UNKNOWN


def start_supervised(attempt, on_failure, reset, backoff_ms=RETRY_BACKOFF_MS):
    """Run `attempt()` until it succeeds; reset the board if it never does.

    - `attempt()` performs one complete startup from a clean slate and returns whatever
      the caller needs to run. It must be safe to call again after it has raised.
    - `on_failure(exc, code, attempt_number, delay_ms)` reports the failure and is
      expected to *take* `delay_ms` doing it — the caller spends that time flashing
      `code` on the LED, which is both the wait and the diagnostic.
    - `reset()` restarts the board.

    Returns whatever `attempt()` returned.
    """
    for number, delay_ms in enumerate(backoff_ms, start=1):
        try:
            return attempt()
        except Exception as exc:
            on_failure(exc, code_for_error(exc), number, delay_ms)

    reset()
    raise StartupFailed("startup failed %d times; board reset" % len(backoff_ms))


__all__ = [
    "CODE_SENSORS", "CODE_MODEM", "CODE_NETWORK", "CODE_BROKER", "CODE_RUNTIME",
    "CODE_UNKNOWN", "RETRY_BACKOFF_MS", "SensorWiringError", "StartupFailed",
    "code_for_error", "start_supervised",
]
