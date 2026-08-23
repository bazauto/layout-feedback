PicoLayoutFeedback — MQTT AT bridge (MicroPython)

Overview
- `mqtt_at.py`: AT-command based MQTT helper for MicroPython. It sends AT MQTT commands
  to an external modem over a UART and parses incoming `+MQTTSUBRECV` lines.
- Designed for simple publish/subscribe integration between input/output modules
  in the Pico-based monitoring app.

Quick usage
- Create the client (constructor takes broker host/port):

  from mqtt_at import MQTTATClient
  client = MQTTATClient('172.18.10.240', 1883, uart_id=1, tx_pin=8, rx_pin=9, debug=True)
  client.setup()

- Subscribe and register an output handler:

  client.subscribe('track/#')
  def handle(topic, payload):
      print(topic, payload)
  client.register_topic_handler('track/', handle)

- Publish from an input module:

  client.publish('track/sensor1', 'payload-data')

Polling
- Call `client.poll()` frequently from your main loop to process UART lines and
  dispatch incoming MQTT messages to registered handlers.

Testing (host)
- Tests live in `tests/`.
- Install pytest (if not already installed):

  python3 -m pip install --user pytest

- Run tests from the project root. Important: ensure the project root is on
  `PYTHONPATH` so the test runner can import `mqtt_at`:

  PYTHONPATH=. pytest -q tests/test_mqtt_parser.py

- A convenience script is provided at `scripts/run_tests.sh`:

  bash scripts/run_tests.sh

Notes
- The module assumes the modem emits ASCII lines terminated by CRLF and uses the
  `+MQTTSUBRECV` response format for incoming messages.
- This README intentionally excludes references to any temporary USB<->UART
  interactive test harnesses.
