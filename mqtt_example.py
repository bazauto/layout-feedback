"""Example usage of MQTTATClient.

Shows creating the client, registering a topic handler, publishing a message,
and polling in a simple loop. Adapt pins/host as required for your hardware.
"""
from mqtt_at import MQTTATClient
from utime import sleep


def output_handler(topic, payload):
    # topic and payload are strings
    print('MQTT IN:', topic, '->', payload)


def main():
    # Change to your broker IP
    HOST = '172.18.10.240'
    PORT = 1883

    # Create client and let it create UART(1) on pins 8/9
    client = MQTTATClient(HOST, PORT, uart_id=1, tx_pin=8, rx_pin=9, baud=9600, debug=True)
    client.setup()

    # Subscribe to a prefix; handler will get (topic, payload)
    client.subscribe('track/#')
    client.register_topic_handler('track/', output_handler)

    # publish a test message
    client.publish('track/test', 'hello from pico')

    client.ping("172.18.10.1")

    try:
        while True:
            client.poll()
            sleep(0.1)
    except KeyboardInterrupt:
        print('Stopping')
        client.close()


if __name__ == '__main__':
    main()
