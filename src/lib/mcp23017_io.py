"""MCP23017 GPIO expander helpers for both input monitoring and digital outputs."""

# Pin index mapping (zero-based) used by MCP23017Pin:
#   0-7  -> GPA0-GPA7 (Port A)
#   8-15 -> GPB0-GPB7 (Port B)
# This mirrors the physical device pins when numbering starts at GPA0.

from machine import I2C
from utime import ticks_ms, ticks_diff


_IODIR = (0x00, 0x01)
_IOCON = (0x0A, 0x0B)
_GPPU = (0x0C, 0x0D)
_GPIO = (0x12, 0x13)
_OLAT = (0x14, 0x15)

_DEFAULT_IOCON = 0x20  # BANK=0, MIRROR=0, SEQOP=1, others cleared


class MCP23017Expander:
    """Coordinate register access for a single MCP23017 device."""

    def __init__(self, i2c: I2C, address: int = 0x20, iocon=None):
        self._i2c = i2c
        self._address = address
        self._iocon = _DEFAULT_IOCON if iocon is None else (iocon & 0x7F)
        # Force BANK=0 (bit7 cleared) so sequential addressing is used.
        self._iocon &= ~(1 << 7)
        self._configure_control_registers()
        self._cache = {
            "iodir": [self._read_u8(_IODIR[0]), self._read_u8(_IODIR[1])],
            "gppu": [self._read_u8(_GPPU[0]), self._read_u8(_GPPU[1])],
            "olat": [self._read_u8(_OLAT[0]), self._read_u8(_OLAT[1])],
        }

    def create_pin(self, pin_number: int, *, mode: str = "input",
                    pull_up: bool = True, debounce_ms: int = 25,
                    initial_state: int = 0) -> "MCP23017Pin":
        return MCP23017Pin(
            self,
            pin_number,
            mode=mode,
            pull_up=pull_up,
            debounce_ms=debounce_ms,
            initial_state=initial_state,
        )

    # Register helpers -------------------------------------------------

    def _write_register(self, register_pair, bank: int, value: int) -> None:
        register = register_pair[bank]
        self._i2c.writeto_mem(self._address, register, bytes([value & 0xFF]))

    def _configure_control_registers(self) -> None:
        for bank in (0, 1):
            self._write_register(_IOCON, bank, self._iocon)

    def _read_u8(self, register: int) -> int:
        return self._i2c.readfrom_mem(self._address, register, 1)[0]

    def _update_bit(self, cache_key: str, register_pair, bank: int,
                    bit_mask: int, set_bit: bool) -> None:
        value = self._cache[cache_key][bank]
        if set_bit:
            value |= bit_mask
        else:
            value &= ~bit_mask
        self._cache[cache_key][bank] = value
        self._write_register(register_pair, bank, value)

    def _read_gpio(self, bank: int) -> int:
        return self._read_u8(_GPIO[bank])

    # Pin-level operations ---------------------------------------------

    def configure_input(self, bank: int, bit_mask: int, *, pull_up: bool) -> None:
        self._update_bit("iodir", _IODIR, bank, bit_mask, True)
        self._update_bit("gppu", _GPPU, bank, bit_mask, pull_up)

    def configure_output(self, bank: int, bit_mask: int, *, pull_up: bool = False) -> None:
        self._update_bit("iodir", _IODIR, bank, bit_mask, False)

    def write_output(self, bank: int, bit_mask: int, level: int) -> None:
        value = self._cache["olat"][bank]
        if level:
            value |= bit_mask
        else:
            value &= ~bit_mask
        self._cache["olat"][bank] = value
        self._write_register(_OLAT, bank, value)

    def read_pin(self, bank: int, bit_mask: int) -> int:
        value = self._read_gpio(bank)
        return 1 if value & bit_mask else 0

    def debug_register_snapshot(self):
        """Return current raw register values for diagnostics."""
        return {
            "IODIR": [self._read_u8(_IODIR[0]), self._read_u8(_IODIR[1])],
            "GPPU": [self._read_u8(_GPPU[0]), self._read_u8(_GPPU[1])],
            "GPIO": [self._read_u8(_GPIO[0]), self._read_u8(_GPIO[1])],
            "OLAT": [self._read_u8(_OLAT[0]), self._read_u8(_OLAT[1])],
        }


class MCP23017Pin:
    """Represents a single MCP23017 pin as either input or output."""

    def __init__(self, device: MCP23017Expander, pin_number: int, *,
                 mode: str = "input", pull_up=None,
                 debounce_ms: int = 25, initial_state: int = 0):
        if not 0 <= pin_number <= 15:
            raise ValueError("pin_number must be between 0 and 15")
        mode_normalized = mode.lower()
        if mode_normalized not in ("input", "output"):
            raise ValueError("mode must be either 'input' or 'output'")

        self._device = device
        self._pin_number = pin_number
        self._bank = 0 if pin_number < 8 else 1
        self._bit_mask = 1 << (pin_number % 8)
        self._mode = mode_normalized
        if pull_up is None:
            pull_up = True if self._mode == "input" else False
        self._pull_up = bool(pull_up)
        self.debounce_ms = debounce_ms
        self._initial_state = 1 if initial_state else 0

        self._configured = False
        self._last_state = self._initial_state if self._mode == "output" else 0
        self._last_change_ms = 0
        # A change in progress: the level seen on the last poll, and when it first
        # appeared. Cleared whenever the input settles back to the accepted state.
        self._candidate_state = None
        self._candidate_since_ms = 0

    def setup(self) -> None:
        if self._configured:
            return

        now = ticks_ms()
        if self._mode == "input":
            self._device.configure_input(self._bank, self._bit_mask,
                                         pull_up=self._pull_up)
            self._last_state = self._device.read_pin(self._bank, self._bit_mask)
            self._last_change_ms = now
        else:
            self._device.configure_output(self._bank, self._bit_mask,
                                          pull_up=self._pull_up)
            self._device.write_output(self._bank, self._bit_mask,
                                      self._initial_state)
            self._last_state = self._initial_state
            self._last_change_ms = now

        self._configured = True

    # Input helpers ----------------------------------------------------

    def update_state(self) -> bool:
        """Return True once a changed level has held steady for `debounce_ms`.

        This is a real debounce, not a rate limit. It used to compare `now` against the
        last *accepted* change, which merely capped how often a change could be reported
        — a genuinely chattering input was still reported as a change every
        `debounce_ms`, which for a sensor on the layout means a stream of contradictory
        occupied/clear readings rather than one settled answer.

        Now a new level must be observed on consecutive polls for the whole debounce
        window before it counts. A level that bounces back cancels the candidate and
        nothing is reported. An input chattering faster than the window never settles,
        so the last stable reading stands — which is the honest answer, since a signal
        that will not sit still is not evidence of anything.
        """
        if self._mode != "input":
            raise RuntimeError("update_state() is only valid for input pins")
        if not self._configured:
            raise RuntimeError("Call setup() before update_state().")

        current_state = self._device.read_pin(self._bank, self._bit_mask)
        now = ticks_ms()

        if current_state == self._last_state:
            # Settled back to where it was; abandon any change in progress.
            self._candidate_state = None
            return False

        if not self.debounce_ms:
            self._last_state = current_state
            self._last_change_ms = now
            self._candidate_state = None
            return True

        if self._candidate_state != current_state:
            self._candidate_state = current_state
            self._candidate_since_ms = now
            return False

        if ticks_diff(now, self._candidate_since_ms) < self.debounce_ms:
            return False

        self._last_state = current_state
        self._last_change_ms = now
        self._candidate_state = None
        return True

    @property
    def state(self) -> int:
        if not self._configured:
            raise RuntimeError("Call setup() before reading state.")
        return self._last_state

    # Output helpers ---------------------------------------------------

    def write(self, level: int) -> None:
        if self._mode != "output":
            raise RuntimeError("write() is only valid for output pins")
        if not self._configured:
            raise RuntimeError("Call setup() before write().")
        sanitized = 1 if level else 0
        self._device.write_output(self._bank, self._bit_mask, sanitized)
        self._last_state = sanitized

    def toggle(self) -> int:
        if self._mode != "output":
            raise RuntimeError("toggle() is only valid for output pins")
        new_level = 0 if self._last_state else 1
        self.write(new_level)
        return new_level
