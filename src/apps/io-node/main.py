"""IO node: MCP23017 block detectors, IR beams and point position feedback to MQTT.

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
from mqtt_at import BrokerUnreachable, MQTTATClient
from node_startup import CODE_RUNTIME, SensorWiringError, start_supervised
from point_wiring import (
    assert_no_pin_overlap, decode_position, is_closed, select_installed_points,
)
from sensor_wiring import is_occupied, select_installed
from status_led import StatusLED

import config


# How long to flash the runtime code before rebooting after a crash in the main loop.
# Long enough to be seen and counted, short enough that the node is not off the air for
# any longer than the diagnosis needs.
RUNTIME_CRASH_FLASH_MS = 5000

# The contract's `point/*/query` is QoS 1.
QUERY_QOS = 1


def get_expander(expanders, i2c, available, address, what):
    """Return the one `MCP23017Expander` for `address`, creating it on first use.

    All pins on a device must come from the same instance, because it caches the register
    state it writes; two expanders for one chip would each overwrite the other's IODIR
    and GPPU.
    """
    if address not in available:
        raise SensorWiringError(
            "expander 0x%02X not on the bus (found %s) — %s cannot be published"
            % (address, [hex(a) for a in available], what))

    if address not in expanders:
        expanders[address] = MCP23017Expander(i2c, address=address)
    return expanders[address]


def build_sensors(i2c, available, expanders, wiring):
    """Configure one input pin per installed sensor."""
    sensors = []

    for entry in wiring:
        expander = get_expander(
            expanders, i2c, available, entry["expander"], entry["sensor_id"])
        pin = expander.create_pin(
            entry["pin"], mode="input", debounce_ms=config.DEBOUNCE_MS)
        pin.setup()
        sensors.append((entry["sensor_id"], pin))

    return sensors


def build_points(i2c, available, expanders, wiring):
    """Configure the two input pins per installed point.

    Both halves of a pair come from the same expander instance, which is what gives them
    a shared failure domain: a chip that drops off the bus fails both together and the
    point reads `unknown`. One live input and one dead one would read as a confident,
    corroborated-looking position that nothing is corroborating.
    """
    points = []

    for entry in wiring:
        expander = get_expander(
            expanders, i2c, available, entry["expander"], entry["point_id"])
        pins = []
        for which in ("normal_pin", "reverse_pin"):
            pin = expander.create_pin(
                entry[which], mode="input", debounce_ms=config.POINT_DEBOUNCE_MS)
            pin.setup()
            pins.append(pin)
        points.append((entry["point_id"], pins[0], pins[1]))

    return points


def read_point(normal_pin, reverse_pin):
    """Decode a point's contact pair into a contract position."""
    return decode_position(
        is_closed(normal_pin.state, config.POINTS_ACTIVE_LOW),
        is_closed(reverse_pin.state, config.POINTS_ACTIVE_LOW),
    )


def report_cross_wiring(point_id):
    print("io-node: point %s reads both contacts closed — impossible on an SPDT "
          "changeover, so the wiring is not what config.py says. Reporting unknown."
          % point_id)


def make_query_handler(layout, point_id):
    """Handle `point/{pointId}/query` by noting it, never by publishing.

    `poll()` dispatches this, and a publish from here would re-enter the modem in the
    middle of whatever command `poll()` was called from. `tick()` answers on the next
    pass, which is safe to be late: a `query` deliberately does not arm the backend's
    confirmation deadline the way a `command` does.
    """
    def handle(_topic, _payload):
        layout.note_query(point_id)

    return handle


def subscribe_to_queries(client, layout, points):
    """Subscribe to each installed point's query topic, and refuse to start without it.

    A failed subscribe is not survivable in the way a failed publish is. The node would
    come up looking healthy, publish its re-asserts, and never answer a query — leaving
    every queried point at `unknown` and every edge through it untraversable, with
    nothing anywhere reporting a fault.
    """
    for point_id, _normal, _reverse in points:
        topic = layout.point_query_topic(point_id)
        if not client.subscribe(topic, qos=QUERY_QOS):
            raise BrokerUnreachable("could not subscribe to %s" % topic)
        client.register_topic_handler(topic, make_query_handler(layout, point_id))


def bring_up():
    """One complete startup attempt, from a clean slate.

    Everything that can fail on the way up lives in here so a retry starts over rather
    than resuming from whatever half-configured state the last failure left behind.
    """
    # Resolve and validate the wiring before touching the modem, so a config mistake
    # fails immediately and obviously rather than as silence on the broker.
    wiring = select_installed(config.SENSORS, config.INSTALLED)
    point_wiring = select_installed_points(config.POINTS, config.POINTS_INSTALLED)
    assert_no_pin_overlap(wiring, point_wiring)
    print("io-node: publishing %d of %d allocated sensors, %d of %d allocated points"
          % (len(wiring), len(config.SENSORS),
             len(point_wiring), len(config.POINTS)))

    i2c = I2C(config.I2C_ID, sda=Pin(config.I2C_SDA), scl=Pin(config.I2C_SCL))
    available = i2c.scan()
    expanders = {}
    sensors = build_sensors(i2c, available, expanders, wiring)
    points = build_points(i2c, available, expanders, point_wiring)

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
        on_publish_failure=lambda topic_id, exc: print(
            "io-node: publish failed for %s (%s)" % (topic_id, exc)),
    )
    for sensor_id, _pin in sensors:
        layout.register_sensor(sensor_id)
    for point_id, _normal, _reverse in points:
        layout.register_point(point_id)

    try:
        subscribe_to_queries(client, layout, points)
    except Exception:
        client.close()
        raise

    # Publish what is on the track right now. Without this the first reading waits for
    # an edge, and a block occupied since before boot would never be reported at all.
    now = ticks_ms()
    for sensor_id, pin in sensors:
        layout.publish_sensor(sensor_id, is_occupied(pin.state, config.ACTIVE_LOW), now)
    for point_id, normal_pin, reverse_pin in points:
        position, cross_wired = read_point(normal_pin, reverse_pin)
        if cross_wired:
            report_cross_wiring(point_id)
        layout.publish_point(point_id, position, now)

    return sensors, points, layout, client


def run(sensors, points, layout, client):
    """The main loop. Returns only on KeyboardInterrupt."""
    while True:
        now = ticks_ms()

        for sensor_id, pin in sensors:
            if pin.update_state():
                layout.publish_sensor(
                    sensor_id, is_occupied(pin.state, config.ACTIVE_LOW), now)

        for point_id, normal_pin, reverse_pin in points:
            # Both, every pass, and never short-circuited: a throw moves one contact
            # before the other, and skipping the second read would decode half a change.
            normal_changed = normal_pin.update_state()
            reverse_changed = reverse_pin.update_state()
            if normal_changed or reverse_changed:
                position, cross_wired = read_point(normal_pin, reverse_pin)
                if cross_wired:
                    report_cross_wiring(point_id)
                layout.publish_point(point_id, position, now)

        # Re-assert anything that has gone quiet, answer any query, and retry anything
        # that failed. Without this every block these sensors feed degrades to `unknown`
        # within the backend's freshness window, and every point configured as requiring
        # feedback degrades to `stale` within its own.
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

    sensors, points, layout, client = start_supervised(bring_up, report, reset)

    led.on()
    print("io-node: running")

    try:
        run(sensors, points, layout, client)
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
