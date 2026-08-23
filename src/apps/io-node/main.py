"""IO node: MCP23017 block detectors and IR beams to the layout MQTT contract.

Deployed as the board's `/main.py`, with `src/lib/*` in `/lib`. All wiring and identity
lives in `config.py`; this file only assembles it and runs the loop.
"""

from machine import I2C, Pin
from utime import sleep_ms, ticks_ms

from layout_mqtt import LayoutMQTT
from mcp23017_io import MCP23017Expander
from mqtt_at import MQTTATClient
from sensor_wiring import is_occupied, select_installed

import config


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
            raise RuntimeError(
                "expander 0x%02X not on the bus (found %s) — %s cannot be published"
                % (address, [hex(a) for a in available], entry["sensor_id"]))

        if address not in expanders:
            expanders[address] = MCP23017Expander(i2c, address=address)

        pin = expanders[address].create_pin(
            entry["pin"], mode="input", debounce_ms=config.DEBOUNCE_MS)
        pin.setup()
        sensors.append((entry["sensor_id"], pin))

    return sensors


def main():
    led = Pin("LED", Pin.OUT)

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
    client.setup()

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

    led.on()
    print("io-node: running")

    try:
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

    except KeyboardInterrupt:
        print("io-node: interrupted")
    finally:
        led.off()


main()
