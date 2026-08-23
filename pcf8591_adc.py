"""PCF8591 8-bit ADC helpers for reading analogue inputs via I2C."""

from machine import I2C
from utime import sleep_ms

_CONTROL_BASE = 0x40  # Single-ended inputs, analogue subsystem enabled
_AUTOINCREMENT_FLAG = 0x04
_MAX_CHANNEL = 3
_ADC_MAX_VALUE = 255


class PCF8591ADC:
    """Coordinate PCF8591 control byte writes and conversions."""

    def __init__(self, i2c: I2C, address: int = 0x48, *, reference_voltage: float = 3.3,
                 auto_increment: bool = False, settle_ms: int = 1):
        if reference_voltage <= 0:
            raise ValueError("reference_voltage must be positive")
        self._i2c = i2c
        self._address = address
        self._reference_voltage = reference_voltage
        self._control_base = _CONTROL_BASE
        if auto_increment:
            self._control_base |= _AUTOINCREMENT_FLAG
        self._settle_ms = max(0, int(settle_ms))
        self._current_channel = None
        self._needs_dummy = True

    def create_channel(self, channel: int, *, reference_voltage=None,
                       change_threshold: int = 2, samples: int = 1) -> "PCF8591AnalogChannel":
        return PCF8591AnalogChannel(
            self,
            channel,
            reference_voltage,
            change_threshold=change_threshold,
            samples=samples,
        )

    def _select_channel(self, channel: int) -> None:
        if self._current_channel == channel:
            return
        control = self._control_base | (channel & 0x03)
        print("Sending control byte: 0x{0:02X} to select channel {1}".format(control, channel))
        self._i2c.writeto(self._address, bytes([control]))
        self._current_channel = channel
        self._needs_dummy = True # After channel change, first read is invalid

    def read_raw(self, channel: int) -> int:
        if not 0 <= channel <= _MAX_CHANNEL:
            raise ValueError("channel must be between 0 and 3")
        self._select_channel(channel)
        raw = self._i2c.readfrom(self._address, 1)[0]
        if self._needs_dummy:
            raw = self._i2c.readfrom(self._address, 1)[0]
            self._needs_dummy = False
        return raw

    def read_voltage(self, channel: int, reference_voltage=None) -> float:
        ref = self._reference_voltage if reference_voltage is None else reference_voltage
        if ref <= 0:
            raise ValueError("reference_voltage must be positive")
        raw = self.read_raw(channel)
        return self.raw_to_voltage(raw, ref)

    def raw_to_voltage(self, raw_value: int, reference_voltage=None) -> float:
        ref = self._reference_voltage if reference_voltage is None else reference_voltage
        if ref <= 0:
            raise ValueError("reference_voltage must be positive")
        clipped = max(0, min(raw_value, _ADC_MAX_VALUE))
        return (clipped / _ADC_MAX_VALUE) * ref


class PCF8591AnalogChannel:
    """Represent a single PCF8591 analogue input channel."""

    def __init__(self, device: "PCF8591ADC", channel: int, reference_voltage,
                 *, change_threshold: int = 2, samples: int = 1):
        self._device = device
        self._channel = channel
        self._reference_voltage = reference_voltage
        self._change_threshold = max(0, int(change_threshold))
        self._samples = max(1, int(samples))
        self._last_raw = None
        self._last_voltage = None

    def read_raw(self) -> int:
        return self._device.read_raw(self._channel)

    def read_voltage(self) -> float:
        return self._device.read_voltage(self._channel, self._reference_voltage)

    def update_value(self) -> bool:
        raw = self._sample_raw()
        last = self._last_raw
        if last is None or abs(raw - last) >= self._change_threshold:
            self._last_raw = raw
            self._last_voltage = self._device.raw_to_voltage(raw, self._reference_voltage)
            return True
        return False

    def _sample_raw(self) -> int:
        if self._samples == 1:
            return self.read_raw()
        total = 0
        for _ in range(self._samples):
            total += self.read_raw()
        return total // self._samples

    @property
    def raw_value(self) -> int:
        if self._last_raw is None:
            raise RuntimeError("Call update_value() before accessing raw_value.")
        return self._last_raw

    @property
    def voltage(self) -> float:
        if self._last_voltage is None:
            raise RuntimeError("Call update_value() before accessing voltage.")
        return self._last_voltage


__all__ = ["PCF8591ADC", "PCF8591AnalogChannel"]
