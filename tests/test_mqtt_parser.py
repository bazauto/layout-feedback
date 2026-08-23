import pytest
from mqtt_at import parse_mqttsubrecv_line


def test_basic_message():
    line = '+MQTTSUBRECV:0,"track/test",11,hello world'
    parsed = parse_mqttsubrecv_line(line)
    assert parsed == ('track/test', 'hello world')


def test_quoted_payload():
    line = '+MQTTSUBRECV:0,"topic/1",13,"hello, world!"'
    parsed = parse_mqttsubrecv_line(line)
    assert parsed == ('topic/1', 'hello, world!')


def test_empty_payload():
    line = '+MQTTSUBRECV:0,"topic/2",0,'
    parsed = parse_mqttsubrecv_line(line)
    assert parsed == ('topic/2', '')


def test_payload_with_commas():
    # length value is present but parser doesn't validate length; payload contains commas
    line = '+MQTTSUBRECV:0,"topic/3",21,one,two,three'
    parsed = parse_mqttsubrecv_line(line)
    assert parsed == ('topic/3', 'one,two,three')


def test_non_matching_line():
    line = 'OK'
    assert parse_mqttsubrecv_line(line) is None
