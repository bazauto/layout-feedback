from machine import Pin, I2C
from utime import sleep

from input_pin_monitor import InputPinMonitor
from mcp23017_io import MCP23017Expander
from pcf8591_adc import PCF8591ADC
from mcu_uart_bridge import UARTBridge
from pn7150_mux_reader import PN7150MuxManager
from mqtt_at import MQTTATClient


def scan_i2c_bus(i2c_instance: I2C):
    devices = i2c_instance.scan()
    if not devices:
        print("No I2C devices detected.")
        return devices
    print("I2C devices detected:")
    for addr in devices:
        print(f" - 0x{addr:02X} ({addr})")
    return devices

led = Pin("LED", Pin.OUT)

# NFC (PN7150 via mux) configuration
NFC_ENABLED = True
NFC_I2C_ID = 1
NFC_SDA = 2
NFC_SCL = 3
NFC_I2C_FREQ = 100_000
NFC_I2C_ADDRESS = 0x28
NFC_MUX_ADDRESS = 0x70

NFC_READERS = [
    {"name": "reader-0", "channel": 0, "irq": 16, "topic": "layout/reader/0", "i2c_address": NFC_I2C_ADDRESS},
    {"name": "reader-1", "channel": 1, "irq": 17, "topic": "layout/reader/1", "i2c_address": NFC_I2C_ADDRESS},
]

# MQTT configuration
MQTT_ENABLED = True
MQTT_HOST = "172.18.10.240"
MQTT_PORT = 1883
MQTT_UART_ID = 1
MQTT_TX = 8
MQTT_RX = 9
MQTT_BAUD = 9600

# IO expander configuration
IO_EXPANDER_ADDRESSES = [0x20, 0x21]

IO_INPUTS = [
    {"name": "mcp20_in0", "expander": 0x20, "pin": 0, "active_low": True, "debounce_ms": 200},
    {"name": "mcp21_in0", "expander": 0x21, "pin": 0, "active_low": True, "debounce_ms": 200},
]

IO_OUTPUTS = [
    {"name": "mcp20_out8", "expander": 0x20, "pin": 8, "initial_state": 0},
    {"name": "mcp21_out8", "expander": 0x21, "pin": 8, "initial_state": 0},
]

i2c = I2C(0, sda=Pin(4), scl=Pin(5))
print("Scanning I2C bus for connected devices...")
available_i2c = scan_i2c_bus(i2c)

expanders: dict[int, MCP23017Expander] = {}
inputs = []
outputs = {}

for addr in IO_EXPANDER_ADDRESSES:
    if addr in available_i2c:
        try:
            expanders[addr] = MCP23017Expander(i2c, address=addr)
        except Exception as exc:
            print("MCP23017 init error:", hex(addr), exc)

for cfg in IO_INPUTS:
    expander = expanders.get(cfg["expander"])
    if expander is None:
        continue
    exp_pin = expander.create_pin(
        cfg["pin"],
        mode="input",
        debounce_ms=cfg.get("debounce_ms", 100),
    )
    exp_pin.setup()
    inputs.append((cfg["name"], exp_pin, bool(cfg.get("active_low", True))))

for cfg in IO_OUTPUTS:
    expander = expanders.get(cfg["expander"])
    if expander is None:
        continue
    exp_pin = expander.create_pin(
        cfg["pin"],
        mode="output",
        initial_state=cfg.get("initial_state", 0),
    )
    exp_pin.setup()
    outputs[cfg["name"]] = exp_pin

# pcf8591 = PCF8591ADC(i2c, address=0x48, reference_voltage=3.3)
# pcf8591_ch0 = pcf8591.create_channel(0, change_threshold=10, samples=4)

print("Starting...")
led.on() # Just to indicate running

mqtt_client = None
nfc_manager = None

if MQTT_ENABLED:
    try:
        mqtt_client = MQTTATClient(host=MQTT_HOST, port=MQTT_PORT, uart_id=MQTT_UART_ID, tx_pin=MQTT_TX, rx_pin=MQTT_RX, baud=MQTT_BAUD)
        mqtt_client.setup()
    except Exception as exc:
        print("MQTT setup error:", exc)
        mqtt_client = None

def _publish_nfc_event(reader_name: str, topic: str, event: str, tag_uid: str, protocol: int, protocol_name: str):
    if mqtt_client is None:
        return
    if event == "activate":
        mqtt_client.publish(topic, tag_uid)
    elif event == "deactivate":
        mqtt_client.publish(topic, "")

if NFC_ENABLED:
    try:
        nfc_i2c = I2C(NFC_I2C_ID, sda=Pin(NFC_SDA), scl=Pin(NFC_SCL), freq=NFC_I2C_FREQ)
        nfc_manager = PN7150MuxManager(nfc_i2c, NFC_MUX_ADDRESS, NFC_READERS, on_event=_publish_nfc_event)
        if not nfc_manager.init():
            print("No NFC readers initialized.")
            nfc_manager = None
    except Exception as exc:
        print("NFC init error:", exc)
        nfc_manager = None

if mqtt_client is not None:
    def _on_sensor_message(topic: str, payload: str):
        prefix = "track/sensor/"
        if not topic.startswith(prefix):
            return
        name = topic[len(prefix):]
        output = outputs.get(name)
        if output is None:
            return
        state = payload.strip().upper()
        if state in ("ACTIVE", "1", "ON", "TRUE"):
            output.write(1)
        elif state in ("INACTIVE", "0", "OFF", "FALSE"):
            output.write(0)

    mqtt_client.register_topic_handler("track/sensor/", _on_sensor_message)
    mqtt_client.subscribe("track/sensor/#")
try:
    while True:

        for name, pin_obj, active_low in inputs:
            while pin_obj.update_state():
                is_active = (pin_obj.state == 0) if active_low else (pin_obj.state == 1)
                if mqtt_client is not None:
                    topic = "track/sensor/" + name
                    mqtt_client.publish(topic, "ACTIVE" if is_active else "INACTIVE")

        # if pcf8591_ch0.update_value():
        #     analog_raw = pcf8591_ch0.raw_value
        #     analog_voltage = pcf8591_ch0.voltage
        #     print(f"PCF8591 channel 0 raw={analog_raw} ({analog_voltage:.2f} V)")

        if nfc_manager is not None:
            nfc_manager.loop()

        if mqtt_client is not None:
            try:
                mqtt_client.poll()
            except Exception:
                pass

        sleep(0.05)

except KeyboardInterrupt:
    print("Interrupted by user.")
    pass

except Exception as exc:
    print("Fatal error:", exc)

led.off()
print("Finished.")
