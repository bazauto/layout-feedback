
# Use in the monitoring app

## Setup
Apply Base config, should get an OK in the response
Comment to the MQTT Broker, should get an OK in the response
### Input module
Use the publish command when a monitor needs to notify. The topic and data will be defined by the monitor.
### Output module
Subscribes to a topic defined by the module and sets the outputs based on the received messages.

# Commands

## Base config
AT+MQTTUSERCFG=0,1,"sensors","","",0,0,""

## Connect to MQTT broker
AT+MQTTCONN=0,"172.18.10.240",1883,1
## Check connection status
AT+MQTTCONN?

## Subscribe to topic "track/#" with QoS 0
AT+MQTTSUB=0,"track/#",0
## Messages to subscribed topics look like this
+MQTTSUBRECV:0,"topic",<data length>,data

## Publish message "esp32-test" to topic "track/test" with QoS 0 and no retain
AT+MQTTPUB=0,"track/test","esp32-test",0,0

## Ping the MQTT broker to check connectivity
AT+PING="172.18.10.240"