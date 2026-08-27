"""IO node: MCP23017 block detectors and IR beams to the layout MQTT contract.

Deployed as the board's `/main.py`, with `src/lib/*` in `/lib`. All wiring and identity
lives in `config.py`; this file only assembles it and runs the loop.

Startup is supervised (`node_startup.py`): a failure on the way up is flashed on the
onboard LED, retried, and eventually met with a board reset, rather than dropping to a
REPL that nobody is watching.
"""

from machine import I2C, Pin, reset
from utime import sleep_ms, ticks_ms

from layout_mqtt import LayoutMQTT
from mcp23017_io import MCP23017Expander
from mqtt_at import MQTTATClient
from node_startup import CODE_RUNTIME, SensorWiringError, start_supervised
from sensor_wiring import is_occupied, select_installed
from status_led import StatusLED

import config


# How long to flash the runtime code before rebooting after a crash in the main loop.
# Long enough to be seen and counted, short enough that the node is not off the air for
# any longer than the diagnosis needs.
RUNTIME_CRASH_FLASH_MS = 5000


def build_sensors(i2c, wiring):
    """Configure one input pin per installed sensor, sharing an expander per address.

    All pins on a device must come from the same `MCP23017Expander`, because it caches
    the register state it writes; two expanders for one chip would each overwrite the
    other's IODIR and GPPU.
    """
    expanders = {}
    available = i2c.scan()
    sensors = []

    for entry in wiring:
        address = entry["expander"]
        if address not in available:
            raise SensorWiringError(
                "expander 0x%02X not on the bus (found %s) — %s cannot be published"
                % (address, [hex(a) for a in available], entry["sensor_id"]))

        if address not in expanders:
            expanders[address] = MCP23017Expander(i2c, address=address)

        pin = expanders[address].create_pin(
            entry["pin"], mode="input", debounce_ms=config.DEBOUNCE_MS)
        pin.setup()
        sensors.append((entry["sensor_id"], pin))

    return sensors


def bring_up():
    """One complete startup attempt, from a clean slate.

    Everything that can fail on the way up lives in here so a retry starts over rather
    than resuming from whatever half-configured state the last failure left behind.
    """
    # Resolve and validate the wiring before touching the modem, so a config mistake
    # fails immediately and obviously rather than as silence on the broker.
    wiring = select_installed(config.SENSORS, config.INSTALLED)
    print("io-node: publishing %d of %d allocated sensors"
          % (len(wiring), len(config.SENSORS)))

    i2c = I2C(config.I2C_ID, sda=Pin(config.I2C_SDA), scl=Pin(config.I2C_SCL))
    sensors = build_sensors(i2c, wiring)

    client = MQTTATClient(
        host=config.MQTT_HOST, port=config.MQTT_PORT,
        uart_id=config.MQTT_UART_ID, tx_pin=config.MQTT_TX_PIN,
        rx_pin=config.MQTT_RX_PIN, baud=config.MQTT_BAUD,
        client_id=config.MQTT_CLIENT_ID,
    )
    try:
        client.setup()
    except Exception:
        # Hand the UART back before the next attempt tries to claim the same pins.
        client.close()
        raise

    layout = LayoutMQTT(
        client, config.LAYOUT_ID,
        on_publish_failure=lambda sensor_id, exc: print(
            "io-node: publish failed for %s (%s)" % (sensor_id, exc)),
    )
    for sensor_id, _pin in sensors:
        layout.register_sensor(sensor_id)

    # Publish what is on the track right now. Without this the first reading waits for
    # an edge, and a block occupied since before boot would never be reported at all.
    now = ticks_ms()
    for sensor_id, pin in sensors:
        layout.publish_sensor(sensor_id, is_occupied(pin.state, config.ACTIVE_LOW), now)

    return sensors, layout, client


def run(sensors, layout, client):
    """The main loop. Returns only on KeyboardInterrupt."""
    while True:
        now = ticks_ms()

        for sensor_id, pin in sensors:
            if pin.update_state():
                layout.publish_sensor(
                    sensor_id, is_occupied(pin.state, config.ACTIVE_LOW), now)

        # Re-assert anything that has gone quiet, and retry anything that failed.
        # Without this every block these sensors feed degrades to `unknown` within
        # the backend's freshness window, and `unknown` is treated as occupied.
        layout.tick(now)

        client.poll()
        sleep_ms(config.LOOP_SLEEP_MS)


def main():
    led = StatusLED(Pin("LED", Pin.OUT))
    led.off()

    def report(exc, code, attempt, delay_ms):
        print("io-node: startup attempt %d failed — %s: %s" % (attempt, type(exc).__name__, exc))
        print("io-node: flashing code %d for %d ms, then retrying" % (code, delay_ms))
        led.flash(code, delay_ms)

    sensors, layout, client = start_supervised(bring_up, report, reset)

    led.on()
    print("io-node: running")

    try:
        run(sensors, layout, client)
    except KeyboardInterrupt:
        print("io-node: interrupted")
        led.off()
    except Exception as exc:
        # A crash in the loop is the same failure as a crash on the way up: the node
        # stops reporting and says nothing about it. Flash it, then come back from cold
        # rather than falling out to a REPL nobody is watching.
        print("io-node: run loop crashed — %s: %s" % (type(exc).__name__, exc))
        led.flash(CODE_RUNTIME, RUNTIME_CRASH_FLASH_MS)
        reset()


main()
