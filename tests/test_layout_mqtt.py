"""The MQTT contract, proven on the host.

Every rule here is one the orchestrator relies on and cannot tell us we broke — a wrong
topic or a missing re-assert produces silence, not an error.
"""

import json

import pytest

from layout_mqtt import (
    REASSERT_INTERVAL_MS,
    LayoutMQTT,
    validate_sensor_id,
)

LAYOUT_ID = "c4e587aa-478d-46ab-a1df-9dd8359fc040"
CS = "cs---goods-shed"
IR = "ir---goods-shed"


class RecordingClient:
    """Stands in for MQTTATClient, recording contract-relevant publish arguments."""

    def __init__(self, succeed=True):
        self.published = []  # (topic, payload, qos, retain)
        self.succeed = succeed

    def publish(self, topic, message, qos=0, retain=0):
        self.published.append((topic, message, qos, retain))
        return self.succeed


@pytest.fixture
def client():
    return RecordingClient()


@pytest.fixture
def layout(client):
    layout = LayoutMQTT(client, LAYOUT_ID)
    layout.register_sensor(CS)
    layout.register_sensor(IR)
    return layout


# --- topics -----------------------------------------------------------------

def test_sensor_topic_matches_the_contract(layout):
    assert layout.sensor_topic(CS) == (
        "layout/c4e587aa-478d-46ab-a1df-9dd8359fc040/sensor/cs---goods-shed/reading")


def test_layout_id_is_not_hardcoded():
    other = LayoutMQTT(RecordingClient(), "00000000-1111-2222-3333-444444444444")
    assert other.topic_base == "layout/00000000-1111-2222-3333-444444444444"


# --- payloads ---------------------------------------------------------------

def test_payload_uses_the_contract_vocabulary(layout):
    assert json.loads(layout.reading_payload(True)) == {"state": "occupied"}
    assert json.loads(layout.reading_payload(False)) == {"state": "clear"}


def test_payload_omits_updated_at(layout):
    """The board has no clock. The contract permits omission; a fabricated timestamp
    would look authoritative and be wrong."""
    assert "updatedAt" not in layout.reading_payload(True)


# --- qos and retention ------------------------------------------------------

def test_readings_are_qos_1_and_retained(layout, client):
    layout.publish_sensor(CS, True, now_ms=0)

    topic, payload, qos, retain = client.published[0]
    assert qos == 1
    assert retain == 1


# --- the re-assert ----------------------------------------------------------

def test_a_state_change_publishes_immediately(layout, client):
    layout.publish_sensor(CS, True, now_ms=1000)

    assert len(client.published) == 1
    assert json.loads(client.published[0][1]) == {"state": "occupied"}


def test_nothing_is_republished_before_the_interval(layout, client):
    layout.publish_sensor(CS, True, now_ms=0)
    client.published.clear()

    layout.tick(REASSERT_INTERVAL_MS - 1)

    assert client.published == []


def test_an_unchanged_sensor_is_republished_after_the_interval(layout, client):
    """The rule the orchestrator's trust model depends on. Without it every block this
    sensor feeds degrades to unknown, which is treated as occupied."""
    layout.publish_sensor(CS, True, now_ms=0)
    client.published.clear()

    assert layout.tick(REASSERT_INTERVAL_MS) == 1

    topic, payload, qos, retain = client.published[0]
    assert topic == layout.sensor_topic(CS)
    assert json.loads(payload) == {"state": "occupied"}, "re-assert must repeat the state"
    assert retain == 1


def test_re_asserts_keep_repeating(layout, client):
    layout.publish_sensor(CS, True, now_ms=0)
    client.published.clear()

    now = 0
    for _ in range(4):
        now += REASSERT_INTERVAL_MS
        layout.tick(now)

    assert len(client.published) == 4


def test_a_publish_resets_the_timer(layout, client):
    layout.publish_sensor(CS, True, now_ms=0)
    layout.publish_sensor(CS, False, now_ms=REASSERT_INTERVAL_MS - 1)
    client.published.clear()

    layout.tick(REASSERT_INTERVAL_MS)
    assert client.published == [], "the interval should run from the last publish"

    layout.tick(2 * REASSERT_INTERVAL_MS)
    assert len(client.published) == 1


def test_a_sensor_that_never_reported_is_never_asserted(layout, client):
    """Registration is not observation. Asserting a state this node has not measured is
    the fabricated reading that the config allow-list exists to prevent."""
    layout.tick(10 * REASSERT_INTERVAL_MS)

    assert client.published == []


def test_each_sensor_keeps_its_own_timer(layout, client):
    layout.publish_sensor(CS, True, now_ms=0)
    layout.publish_sensor(IR, True, now_ms=10_000)
    client.published.clear()

    layout.tick(REASSERT_INTERVAL_MS)

    assert [t for t, _, _, _ in client.published] == [layout.sensor_topic(CS)]


def test_the_clock_wrapping_does_not_stall_the_re_assert(layout, client):
    """`ticks_ms()` wraps. Plain subtraction would go negative and make an overdue
    sensor look freshly published, silently stopping its re-assert."""
    layout.publish_sensor(CS, True, now_ms=2**30)
    client.published.clear()

    assert layout.tick(5) == 1, "a wrapped counter must read as overdue, not as fresh"


# --- failures ---------------------------------------------------------------

def test_a_failed_publish_is_reported():
    client = RecordingClient(succeed=False)
    seen = []
    layout = LayoutMQTT(client, LAYOUT_ID, on_publish_failure=lambda sid, exc: seen.append(sid))
    layout.register_sensor(CS)

    assert layout.publish_sensor(CS, True, now_ms=0) is False
    assert seen == [CS]


def test_a_failed_publish_retries_on_the_next_tick_not_a_full_interval_later():
    client = RecordingClient(succeed=False)
    layout = LayoutMQTT(client, LAYOUT_ID)
    layout.register_sensor(CS)
    layout.publish_sensor(CS, True, now_ms=0)
    client.published.clear()

    client.succeed = True
    layout.tick(1)

    assert len(client.published) == 1, "a lost reading must not wait 25 s to be retried"
    assert json.loads(client.published[0][1]) == {"state": "occupied"}


def test_publishing_an_unregistered_sensor_raises(layout):
    with pytest.raises(ValueError):
        layout.publish_sensor("not-registered", True, now_ms=0)


# --- id validation ----------------------------------------------------------

@pytest.mark.parametrize("bad", ["<tbd>", "REPLACE-ME-block-1", "xxx-sensor", "TBD"])
def test_placeholder_ids_are_refused(bad):
    """A placeholder publishes to a topic nobody subscribes to, and the node looks
    perfectly healthy while the orchestrator sees nothing."""
    with pytest.raises(ValueError):
        validate_sensor_id(bad)


@pytest.mark.parametrize("bad", ["a/b+c", "sensor#1", "has space"])
def test_ids_that_would_escape_their_namespace_are_refused(bad):
    with pytest.raises(ValueError):
        validate_sensor_id(bad)


@pytest.mark.parametrize("bad", ["", None, 42])
def test_empty_or_non_string_ids_are_refused(bad):
    with pytest.raises(ValueError):
        validate_sensor_id(bad)


def test_real_ids_are_accepted():
    for good in (CS, IR, "cs---engine-goods-transfer", LAYOUT_ID):
        assert validate_sensor_id(good) == good
