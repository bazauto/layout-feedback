"""Installs the MicroPython stubs before any device module is imported.

pytest imports `conftest.py` before it collects test modules, which is the only window
in which `sys.modules` can be primed. A device module imported earlier than this would
bind the real (absent) `machine` and fail at collection.

`mqtt_at` is the one module that already runs on the host today: it guards its
`machine` import in a try/except and falls back to `time` for its clock. The stubs do
not change that — the `clock` fixture patches its two module-level time helpers instead,
so a test can drive its timeouts without waiting for them.
"""

import sys
import types

import pytest

import fakes

# --- machine ---------------------------------------------------------------

_machine = types.ModuleType("machine")
_machine.Pin = fakes.FakePin
_machine.Timer = fakes.FakeTimer
_machine.I2C = fakes.FakeI2C
_machine.UART = fakes.FakeUART
sys.modules["machine"] = _machine

# --- utime -----------------------------------------------------------------

_utime = types.ModuleType("utime")
_utime.ticks_ms = fakes.ticks_ms
_utime.ticks_diff = fakes.ticks_diff
_utime.sleep_ms = fakes.sleep_ms
_utime.sleep = fakes.sleep
sys.modules["utime"] = _utime


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    """A clock reset to zero for every test, with `mqtt_at`'s time helpers patched onto it.

    Autouse, because a clock left where the previous test finished is the kind of
    cross-test coupling that produces a failure nobody can reproduce alone.
    """
    fakes.clock.__init__(start_ms=0)

    import mqtt_at

    monkeypatch.setattr(mqtt_at, "_now_ms", fakes.clock.ticks_ms)
    monkeypatch.setattr(mqtt_at, "_sleep_ms", fakes.clock.advance)

    return fakes.clock


@pytest.fixture
def uart():
    return fakes.FakeUART()
