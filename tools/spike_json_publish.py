"""Bench spike: does this modem survive JSON through AT+MQTTPUB, and what do the sensors read?

Run it on the board with `mpremote run`, which executes without writing anything to the
filesystem:

    mpremote connect id:0d9a62134acbf42d run tools/spike_json_publish.py

## Why this exists

The MQTT contract wants JSON payloads. `AT+MQTTPUB` takes its payload as a **quoted**
argument, so every `"` in the JSON has to be escaped or it terminates the argument early
and the command becomes malformed. `mqtt_at._escape_at_string()` escapes `\` and `"`,
which *should* be enough — but whether a given ESP-AT firmware honours that escaping is a
fact about the hardware, not something to reason about from the docs.

Finding out now costs an afternoon. Finding out after the contract layer is built on top
of it costs the design. See #2 and `docs/plans/2026-08-23-node-split.md` D7.

## Safety

**Publishes to a scratch topic, never to a real sensor topic, and never retained.** A
retained message on `layout/{layoutId}/sensor/{id}/reading` would assert layout state that
this firmware is not yet maintaining, and would then go stale on the broker. The topic
below sits outside the `layout/` tree entirely so the orchestrator cannot see it at all.

Reading the sensors is safe: it configures the expander pins exactly the way `main.py`
already does, and reads.
"""

from machine import I2C, Pin
from utime import sleep_ms

try:
    import ujson as json
except ImportError:
    import json

from mqtt_at import MQTTATClient
from mcp23017_io import MCP23017Expander

# --- Configuration ---------------------------------------------------------

MQTT_HOST = "172.18.10.240"
MQTT_PORT = 1883
MQTT_UART_ID = 1
MQTT_TX = 8
MQTT_RX = 9
MQTT_BAUD = 9600

SCRATCH_TOPIC = "spike/layout-feedback/json"

# Goods Shed, both boards, pin 8 (GPB0). See docs/pin-allocation.md.
SENSOR_PINS = (
    (0x20, 8, "cs---goods-shed"),
    (0x21, 8, "ir---goods-shed"),
)

I2C_SDA = 4
I2C_SCL = 5


def read_sensors():
    """Report the raw level of each wired sensor pin.

    Both sensors are known-occupied while this runs (a loco is standing in Goods Shed and
    obscuring the beam), so whatever comes back here *is* the occupied level. That settles
    `active_low` empirically instead of by assumption — which matters, because getting the
    polarity backwards would publish `clear` for an occupied block, and for a
    block_detection sensor that is enough on its own to let the orchestrator clear it.
    """
    print("--- sensors ---")
    i2c = I2C(0, sda=Pin(I2C_SDA), scl=Pin(I2C_SCL))
    found = i2c.scan()
    print("i2c devices:", [hex(a) for a in found])

    for address, pin_number, sensor_id in SENSOR_PINS:
        if address not in found:
            print("  %s: expander 0x%02X NOT PRESENT" % (sensor_id, address))
            continue
        try:
            expander = MCP23017Expander(i2c, address=address)
            pin = expander.create_pin(pin_number, mode="input", debounce_ms=0)
            pin.setup()
            level = pin.state
            print("  %s: 0x%02X pin %d reads %d  (occupied => active_low=%s)"
                  % (sensor_id, address, pin_number, level, level == 0))
        except Exception as exc:
            print("  %s: read failed: %s" % (sensor_id, exc))


def publish_payloads(client):
    """Publish the real contract payload, then a deliberately hostile one."""
    results = []

    # 1. Exactly what the contract layer will send. `updatedAt` is omitted (D4): the board
    #    has no RTC, and the contract permits omission rather than a fabricated timestamp.
    contract_payload = json.dumps({"state": "occupied"})
    print("--- publish 1: contract payload ---")
    print("  payload:", contract_payload)
    ok = client.send_at_and_wait(
        'AT+MQTTPUB=0,"%s","%s",1,0'
        % (client._escape_at_string(SCRATCH_TOPIC), client._escape_at_string(contract_payload))
    )
    print("  modem said:", "OK" if ok else "ERROR/timeout")
    results.append(("contract", contract_payload, ok))

    sleep_ms(500)

    # 2. The boundary case. If escaping works at all it works here: an embedded escaped
    #    quote and a literal backslash are the two characters that can break out of the
    #    AT argument.
    hostile_payload = json.dumps({"note": 'a "quoted" word and a \\ backslash'})
    print("--- publish 2: hostile payload ---")
    print("  payload:", hostile_payload)
    ok = client.send_at_and_wait(
        'AT+MQTTPUB=0,"%s","%s",1,0'
        % (client._escape_at_string(SCRATCH_TOPIC), client._escape_at_string(hostile_payload))
    )
    print("  modem said:", "OK" if ok else "ERROR/timeout")
    results.append(("hostile", hostile_payload, ok))

    return results


def main():
    print("=== spike: JSON through AT+MQTTPUB ===")

    read_sensors()

    print("--- modem ---")
    client = MQTTATClient(
        host=MQTT_HOST, port=MQTT_PORT, uart_id=MQTT_UART_ID,
        tx_pin=MQTT_TX, rx_pin=MQTT_RX, baud=MQTT_BAUD, debug=True,
    )
    try:
        client.setup()
        print("  connected to %s:%d" % (MQTT_HOST, MQTT_PORT))
    except Exception as exc:
        print("  SETUP FAILED:", exc)
        return

    results = publish_payloads(client)

    print("--- summary ---")
    for name, payload, ok in results:
        print("  %-9s %s  %s" % (name, "OK   " if ok else "FAILED", payload))
    print("Now check the broker: what arrived is what matters, not what the modem said.")


main()
