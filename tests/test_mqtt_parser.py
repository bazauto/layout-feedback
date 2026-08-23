"""Length-aware parsing of `+MQTTSUBRECV` frames.

The modem's declared byte count is now honoured. It previously was not: the parser took
everything after the third comma to the end of the line, which is indistinguishable from
correct for `ACTIVE`/`INACTIVE` and wrong for anything containing a comma count it did
not expect, a quote, or a CRLF. JSON contains all three. See #2.
"""

import mqtt_at
from mqtt_at import parse_mqttsubrecv_line


def frame(topic, payload):
    """Build a frame the way the modem does, with a correct declared length."""
    return '+MQTTSUBRECV:0,"%s",%d,%s' % (topic, len(payload), payload)


# --- single complete frames ------------------------------------------------

def test_basic_message():
    assert parse_mqttsubrecv_line(frame("track/test", "hello world")) == (
        "track/test", "hello world")


def test_empty_payload():
    assert parse_mqttsubrecv_line('+MQTTSUBRECV:0,"topic/2",0,') == ("topic/2", "")


def test_payload_containing_commas():
    """Commas in the payload are not delimiters once the length is known."""
    assert parse_mqttsubrecv_line(frame("topic/3", "one,two,three")) == (
        "topic/3", "one,two,three")


def test_payload_containing_quotes():
    """A quote used to truncate the payload or leave a stray one attached."""
    assert parse_mqttsubrecv_line(frame("topic/4", 'say "hi"')) == ("topic/4", 'say "hi"')


def test_json_payload_round_trips():
    """The case the contract actually requires."""
    payload = '{"state": "occupied"}'
    assert parse_mqttsubrecv_line(frame("layout/x/sensor/y/reading", payload)) == (
        "layout/x/sensor/y/reading", payload)


def test_non_matching_line():
    assert parse_mqttsubrecv_line("OK") is None


def test_frame_shorter_than_its_declared_length_is_incomplete():
    """Not a short message — an unfinished one.

    The old parser returned the truncated bytes as though they were the whole payload,
    which is how a split frame became silent corruption rather than a wait.
    """
    assert parse_mqttsubrecv_line('+MQTTSUBRECV:0,"topic/5",21,one,two,three') is None


# --- streaming, via the reader poll() uses ---------------------------------

def take_all(buf):
    """Drain every complete frame from a buffer, returning them and the remainder."""
    frames = []
    while True:
        taken = mqtt_at._take_frame(buf)
        if taken is None:
            return frames, buf
        kind, value, consumed = taken
        frames.append((kind, value))
        buf = buf[consumed:]


def test_payload_containing_crlf_is_one_frame_not_two():
    """The failure that makes JSON unusable: a CRLF inside the payload.

    Splitting on CRLF yields a truncated message plus a garbage line, and the garbage
    line is then parsed as though it were a modem response.
    """
    payload = "line1\r\nline2"
    buf = frame("topic/6", payload).encode() + b"\r\n"

    frames, rest = take_all(buf)

    assert frames == [("message", ("topic/6", payload))]
    assert rest == b""


def test_a_frame_split_across_three_reads():
    """A payload arriving in pieces must not be consumed until it is whole."""
    whole = frame("topic/7", '{"state": "occupied"}').encode() + b"\r\n"
    first, second, third = whole[:20], whole[20:35], whole[35:]

    frames, buf = take_all(first)
    assert frames == [], "consumed an incomplete frame"

    frames, buf = take_all(buf + second)
    assert frames == [], "consumed an incomplete frame"

    frames, buf = take_all(buf + third)
    assert frames == [("message", ("topic/7", '{"state": "occupied"}'))]
    assert buf == b""


def test_a_frame_followed_immediately_by_a_status_line():
    """Both arrive in one read; the status line must survive the frame ahead of it."""
    buf = frame("topic/8", "ACTIVE").encode() + b"\r\nOK\r\n"

    frames, rest = take_all(buf)

    assert frames == [("message", ("topic/8", "ACTIVE")), ("line", "OK")]
    assert rest == b""


def test_lines_and_frames_interleave():
    buf = b"+MQTTCONNECTED:0\r\n" + frame("t", "x").encode() + b"\r\nOK\r\n"

    frames, rest = take_all(buf)

    assert frames == [
        ("line", "+MQTTCONNECTED:0"),
        ("message", ("t", "x")),
        ("line", "OK"),
    ]
    assert rest == b""


def test_a_malformed_frame_does_not_wedge_the_buffer():
    """A garbled delivery must not block every later frame behind it forever."""
    buf = b'+MQTTSUBRECV:0,"topic",notanumber,data\r\nOK\r\n'

    frames, rest = take_all(buf)

    assert ("line", "OK") in frames, "traffic after the bad frame was lost"
    assert rest == b""
