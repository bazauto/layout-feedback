"""`point/{pointId}/reading` against the binding contract.

The rules that differ from a sensor reading, and why:

- **Never retained.** A sensor's occupancy self-corrects on the next movement. A point's
  position can change while its controller is offline — hand-thrown during a shutdown,
  power lost mid-travel — and nothing on the layout corrects it afterwards.
- **Answers a query.** New behaviour for this repo; the node only published before.
- **Same 30 s re-assert** (orchestration#167), but for a different reason: it is the only
  thing that says the controller is alive, since nothing is retained and a fire-and-forget
  DCC command keeps succeeding long after this node has stopped.
"""

import json

import pytest

from layout_mqtt import (
    POINT_QOS, POINT_RETAIN, POSITION_NORMAL, POSITION_REVERSE, POSITION_UNKNOWN,
    REASSERT_INTERVAL_MS, LayoutMQTT,
)

LAYOUT_ID = "c4e587aa-478d-46ab-a1df-9dd8359fc040"
P1 = "88d5527d-c7db-40ab-b27b-532503487c32"
P2 = "acc2150b-ffa1-4733-b2ff-1e4d2147dfe8"

# Every field the backend's `.strict()` Zod schema accepts. An extra one is a malformed
# payload, which is a Fail-Safe Trigger.
ALLOWED_FIELDS = {"pointId", "position", "source", "updatedAt"}


class RecordingClient:
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
    layout.register_point(P1)
    layout.register_point(P2)
    return layout


# --- topics -----------------------------------------------------------------

def test_point_topic_matches_the_contract(layout):
    assert layout.point_topic(P1) == (
        "layout/%s/point/%s/reading" % (LAYOUT_ID, P1))


def test_query_topic_matches_the_contract(layout):
    assert layout.point_query_topic(P1) == (
        "layout/%s/point/%s/query" % (LAYOUT_ID, P1))


# --- payload ----------------------------------------------------------------

def test_payload_carries_exactly_the_contract_fields(layout):
    payload = json.loads(layout.point_reading_payload(P1, POSITION_NORMAL))

    assert payload == {"pointId": P1, "position": "normal", "source": "sensor"}
    assert set(payload) <= ALLOWED_FIELDS


def test_no_extra_field_is_ever_added(layout):
    """The backend's schema is `.strict()` — an extra field Safe-Stops the layout."""
    for position in (POSITION_NORMAL, POSITION_REVERSE, POSITION_UNKNOWN):
        payload = json.loads(layout.point_reading_payload(P1, position))
        assert set(payload) <= ALLOWED_FIELDS


def test_the_point_id_in_the_payload_matches_the_topic_segment(layout, client):
    """A mismatch between the two is a Fail-Safe Trigger."""
    layout.publish_point(P1, POSITION_NORMAL, 0)

    topic, payload, _qos, _retain = client.published[-1]
    assert json.loads(payload)["pointId"] == topic.split("/")[-2]


def test_source_is_always_sensor(layout):
    """This node commands nothing, so it can never honestly claim `driver`.

    A `driver` reading is a delivery acknowledgement, not a position confirmation, and
    never confirms a point configured as requiring feedback.
    """
    assert json.loads(layout.point_reading_payload(P1, POSITION_NORMAL))["source"] == "sensor"


def test_unknown_is_a_publishable_position(layout, client):
    """Unlike a command, a reading MAY be `unknown` — and must be, mid-throw."""
    layout.publish_point(P1, POSITION_UNKNOWN, 0)

    assert json.loads(client.published[-1][1])["position"] == "unknown"


def test_a_position_outside_the_vocabulary_is_refused(layout):
    with pytest.raises(ValueError, match="vocabulary"):
        layout.publish_point(P1, "mid-travel", 0)


def test_an_unregistered_point_is_refused(layout):
    with pytest.raises(ValueError, match="never registered"):
        layout.publish_point("00000000-0000-0000-0000-000000000000", POSITION_NORMAL, 0)


# --- QoS and retention ------------------------------------------------------

def test_a_reading_is_qos_1_and_never_retained(layout, client):
    layout.publish_point(P1, POSITION_NORMAL, 0)

    _topic, _payload, qos, retain = client.published[-1]
    assert qos == 1
    assert retain == 0, "a retained point position is a belief with no correction path"


def test_the_retain_constant_itself_is_off():
    assert POINT_RETAIN == 0
    assert POINT_QOS == 1


# --- the re-assert ----------------------------------------------------------

def test_a_point_is_re_asserted_when_it_goes_quiet(layout, client):
    layout.publish_point(P1, POSITION_NORMAL, 0)
    before = len(client.published)

    layout.tick(REASSERT_INTERVAL_MS)

    assert len(client.published) > before
    assert json.loads(client.published[-1][1])["position"] == "normal"


def test_a_point_is_not_re_asserted_early(layout, client):
    layout.publish_point(P1, POSITION_NORMAL, 0)
    before = len(client.published)

    layout.tick(REASSERT_INTERVAL_MS - 1)

    assert len(client.published) == before


def test_the_interval_leaves_room_inside_the_backends_window():
    """POINT_FRESHNESS_TIMEOUT_MS is 90 s, so two re-asserts can be lost."""
    assert REASSERT_INTERVAL_MS * 3 <= 90_000


def test_a_point_never_read_is_not_asserted(layout, client):
    """An unread pair would assert `unknown` for a point nothing is watching."""
    layout.tick(REASSERT_INTERVAL_MS * 10)

    assert client.published == []


def test_a_failed_publish_is_retried_on_the_next_pass():
    client = RecordingClient(succeed=False)
    layout = LayoutMQTT(client, LAYOUT_ID)
    layout.register_point(P1)

    layout.publish_point(P1, POSITION_NORMAL, 0)
    before = len(client.published)

    layout.tick(1)

    assert len(client.published) > before, \
        "a lost reading must not be held for a full interval"


def test_a_publish_failure_is_reported():
    seen = []
    client = RecordingClient(succeed=False)
    layout = LayoutMQTT(client, LAYOUT_ID,
                        on_publish_failure=lambda topic_id, exc: seen.append(topic_id))
    layout.register_point(P1)

    layout.publish_point(P1, POSITION_NORMAL, 0)

    assert seen == [P1]


# --- answering a query ------------------------------------------------------

def test_a_query_is_answered_on_the_next_tick(layout, client):
    layout.publish_point(P1, POSITION_REVERSE, 0)
    before = len(client.published)

    assert layout.note_query(P1) is True
    layout.tick(1)  # far short of the re-assert interval

    assert len(client.published) == before + 1
    topic, payload, _qos, retain = client.published[-1]
    assert topic == layout.point_topic(P1)
    assert json.loads(payload)["position"] == "reverse"
    assert retain == 0


def test_noting_a_query_does_not_itself_publish(layout, client):
    """Publishing from the message callback would re-enter the modem inside `poll()`."""
    layout.publish_point(P1, POSITION_NORMAL, 0)
    before = len(client.published)

    layout.note_query(P1)

    assert len(client.published) == before


def test_a_query_for_an_unknown_point_is_ignored(layout, client):
    """Answering for a point this node cannot see would be a fabricated reading."""
    assert layout.note_query("00000000-0000-0000-0000-000000000000") is False

    layout.tick(1)
    assert client.published == []


def test_a_query_before_the_first_read_is_not_answered(layout, client):
    """There is nothing honest to say yet."""
    assert layout.note_query(P1) is False

    layout.tick(1)
    assert client.published == []


def test_answering_a_query_resets_the_re_assert_timer(layout, client):
    layout.publish_point(P1, POSITION_NORMAL, 0)
    layout.note_query(P1)
    layout.tick(1)
    before = len(client.published)

    layout.tick(REASSERT_INTERVAL_MS)  # would be due measured from 0, not from the answer

    assert len(client.published) == before


def test_one_points_query_does_not_answer_for_another(layout, client):
    layout.publish_point(P1, POSITION_NORMAL, 0)
    layout.publish_point(P2, POSITION_REVERSE, 0)
    before = len(client.published)

    layout.note_query(P1)
    layout.tick(1)

    assert len(client.published) == before + 1
    assert client.published[-1][0] == layout.point_topic(P1)


# --- sensors and points share one tick --------------------------------------

def test_sensors_and_points_are_both_re_asserted_by_one_tick(client):
    layout = LayoutMQTT(client, LAYOUT_ID)
    layout.register_sensor("cs---goods-shed")
    layout.register_point(P1)
    layout.publish_sensor("cs---goods-shed", True, 0)
    layout.publish_point(P1, POSITION_NORMAL, 0)
    client.published.clear()

    published = layout.tick(REASSERT_INTERVAL_MS)

    assert published == 2
    topics = {topic for topic, _p, _q, _r in client.published}
    assert topics == {layout.sensor_topic("cs---goods-shed"), layout.point_topic(P1)}
