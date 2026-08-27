"""The MQTT contract, as a layer above the transport.

`mqtt_at` moves bytes to and from an ESP-AT modem and knows nothing about sensors.
Everything the orchestrator's `docs/mqtt-contract.md` specifies lives here: topic shape,
payload vocabulary, QoS, retention, and the re-assert that keeps a reading trusted.

**This module imports no `machine`.** That is deliberate and worth preserving — it is
what lets the contract rules be proven by the host suite instead of by deploying and
watching. If a hardware detail needs to reach this layer, pass it in.

## The rule that matters most

A sensor reading must be re-asserted at least every 30 seconds, unchanged, whether or
not it changed. A sensor from which no *live* message arrives inside the backend's
freshness window (`SENSOR_FRESHNESS_TIMEOUT_MS`, 90 s by default) stops being trusted,
and every block it feeds degrades to `unknown` — which the fail-safe rule treats as
occupied. Publishing on change only is the single highest-severity mistake available in
this file. Silence is not consent.

## Why there is no Last Will here

The contract's `system/status` belongs to the **orchestrator**, which registers it as
its own LWT and publishes it retained; a received `"offline"` on it is also a Safe-Stop
trigger. A sensor node publishing a will there would overwrite the orchestrator's own
status when it died. Device liveness is the re-assert plus the freshness window — that
is the mechanism the contract already designs for, and it needs nothing from us but
honest, regular publishing.
"""

try:
    import ujson as json
except ImportError:
    import json


# Contract vocabulary. Not "ACTIVE"/"INACTIVE", which is what this repo used to send.
STATE_OCCUPIED = "occupied"
STATE_CLEAR = "clear"

# Contract requires QoS 1 and retention for sensor readings.
SENSOR_QOS = 1
SENSOR_RETAIN = 1

# Point position vocabulary. Unlike a command, a reading MAY be `unknown`.
POSITION_NORMAL = "normal"
POSITION_REVERSE = "reverse"
POSITION_UNKNOWN = "unknown"

# This node reads changeover contacts, so everything it reports is independently sensed.
# It commands nothing and must never claim `driver`, which is a delivery acknowledgement
# rather than a position confirmation and never confirms a point requiring feedback.
SOURCE_SENSOR = "sensor"

# `point/*/reading` is QoS 1 and explicitly **NOT** retained. A sensor's occupancy
# self-corrects on the next movement; a point's position can change while its controller
# is offline — hand-thrown during a shutdown, power lost mid-travel, a linkage dropped —
# and nothing on the layout corrects it afterwards. A retained point reading is a
# confident assertion with no correction path. Restart recovery is `point/*/query`.
POINT_QOS = 1
POINT_RETAIN = 0

# Comfortably inside the contract's 30 s, which is itself comfortably inside the
# backend's 90 s freshness window. Two whole re-asserts can be lost before a sensor
# stops being trusted.
REASSERT_INTERVAL_MS = 25_000

# Rejected at startup rather than published. A placeholder reaching the broker is worse
# than failing to start: it looks like a real sensor reporting.
_PLACEHOLDER_MARKERS = ("<", ">", "tbd", "TBD", "REPLACE", "replace-me", "xxx", "XXX")


class LayoutMQTT:
    """Publishes sensor readings to the layout contract, and keeps them fresh.

    Usage from a node's main loop:

        layout = LayoutMQTT(client, LAYOUT_ID)
        layout.register_sensor("cs---goods-shed")
        ...
        layout.publish_sensor("cs---goods-shed", occupied=True, now_ms=ticks_ms())
        layout.tick(ticks_ms())       # every loop; re-asserts what has gone quiet
    """

    def __init__(self, client, layout_id, *, reassert_interval_ms=REASSERT_INTERVAL_MS,
                 on_publish_failure=None):
        validate_sensor_id(layout_id, what="layout id")
        self._client = client
        self._layout_id = layout_id
        self._reassert_interval_ms = reassert_interval_ms
        self._on_publish_failure = on_publish_failure
        # sensor_id -> [last_state_or_None, last_published_ms, pending]
        #
        # `pending` is a flag rather than a timestamp trick. Marking a failed publish by
        # backdating `last_published_ms` looks equivalent and is not: a failure at the
        # very start of the clock leaves the timestamp at its initial value, which reads
        # as "just published", and the reading is then held for a full interval.
        self._sensors = {}
        # point_id -> [last_position_or_None, last_published_ms, pending, query_pending]
        #
        # `query_pending` is set by `note_query()` from a message callback and answered
        # by `tick()`. Publishing from inside the callback would re-enter the modem:
        # `poll()` dispatches the handler, and a publish from there calls `poll()` again
        # underneath it.
        self._points = {}

    # -- topics ----------------------------------------------------------

    @property
    def topic_base(self):
        return "layout/%s" % self._layout_id

    def sensor_topic(self, sensor_id):
        return "%s/sensor/%s/reading" % (self.topic_base, sensor_id)

    def point_topic(self, point_id):
        return "%s/point/%s/reading" % (self.topic_base, point_id)

    def point_query_topic(self, point_id):
        return "%s/point/%s/query" % (self.topic_base, point_id)

    # -- registration ----------------------------------------------------

    def register_sensor(self, sensor_id):
        """Declare a sensor this node will publish.

        Registration is not publication: nothing is sent until `publish_sensor()` is
        called with a real reading. A registered sensor that has never reported is not
        re-asserted either, because there is nothing to re-assert — asserting a state
        this node has not observed is exactly the fabricated reading the allow-list in
        `config.py` exists to prevent.
        """
        validate_sensor_id(sensor_id)
        if sensor_id not in self._sensors:
            self._sensors[sensor_id] = [None, 0, False]
        return sensor_id

    @property
    def sensor_ids(self):
        return sorted(self._sensors)

    def last_state(self, sensor_id):
        entry = self._sensors.get(sensor_id)
        return None if entry is None else entry[0]

    # -- payloads --------------------------------------------------------

    @staticmethod
    def reading_payload(occupied):
        """The contract's sensor reading.

        `updatedAt` is deliberately absent. The board has no RTC and no NTP; the
        contract makes the field optional, explicitly permits a device with no
        synchronised clock to omit it, and never makes a safety decision from a
        device-supplied timestamp anyway. A boot-relative value would look
        authoritative and be wrong.
        """
        return json.dumps({"state": STATE_OCCUPIED if occupied else STATE_CLEAR})

    # -- publishing ------------------------------------------------------

    def publish_sensor(self, sensor_id, occupied, now_ms):
        """Publish a reading now, and reset that sensor's re-assert timer.

        Returns whether the modem accepted it. A False is a real event — the reading
        did not reach the broker — and is passed to `on_publish_failure` if one was
        given. It deliberately does **not** update the sensor's last-published time, so
        `tick()` retries on the next pass rather than waiting a full interval.
        """
        if sensor_id not in self._sensors:
            raise ValueError("sensor %r was never registered" % sensor_id)

        ok = self._publish(sensor_id, occupied, now_ms)
        entry = self._sensors[sensor_id]
        entry[0] = bool(occupied)
        if ok:
            entry[1] = now_ms
            entry[2] = False
        else:
            # Leave the timestamp alone and mark it pending, so `tick()` retries on the
            # very next pass rather than holding a lost reading for a full interval.
            entry[2] = True
        return ok

    # -- points ----------------------------------------------------------

    def register_point(self, point_id):
        """Declare a point this node will report the position of.

        As with sensors, registration is not publication. A registered point that has
        never been read is not re-asserted, because an unread pair would assert
        `unknown` for a point nothing is watching.
        """
        validate_sensor_id(point_id, what="point id")
        if point_id not in self._points:
            self._points[point_id] = [None, 0, False, False]
        return point_id

    @property
    def point_ids(self):
        return sorted(self._points)

    def last_position(self, point_id):
        entry = self._points.get(point_id)
        return None if entry is None else entry[0]

    def point_reading_payload(self, point_id, position):
        """The contract's point reading.

        The backend's schema is `.strict()` — an unexpected field is a malformed
        payload, which is a Fail-Safe Trigger — so this carries exactly `pointId`,
        `position` and `source` and nothing else. `updatedAt` is optional there and is
        omitted for the same reason it is omitted from a sensor reading: the board has
        no RTC and no NTP, and a boot-relative value would look authoritative and be
        wrong.

        `pointId` must equal the topic segment. A mismatch is a Fail-Safe Trigger, which
        is why both come from the same argument rather than from two lookups.
        """
        return json.dumps({
            "pointId": point_id,
            "position": position,
            "source": SOURCE_SENSOR,
        })

    def publish_point(self, point_id, position, now_ms):
        """Publish a position now, and reset that point's re-assert timer.

        Returns whether the modem accepted it. As with `publish_sensor()`, a False
        deliberately does not update the last-published time, so `tick()` retries on the
        next pass rather than holding a lost reading for a full interval.
        """
        if point_id not in self._points:
            raise ValueError("point %r was never registered" % point_id)
        if position not in (POSITION_NORMAL, POSITION_REVERSE, POSITION_UNKNOWN):
            raise ValueError(
                "position %r is not in the contract's vocabulary" % (position,))

        ok = self._publish_point(point_id, position, now_ms)
        entry = self._points[point_id]
        entry[0] = position
        entry[3] = False  # any publish answers an outstanding query
        if ok:
            entry[1] = now_ms
            entry[2] = False
        else:
            entry[2] = True
        return ok

    def note_query(self, point_id):
        """Record that the backend asked for this point's position.

        Called from a `point/{pointId}/query` message handler, which must not publish:
        `poll()` dispatches the handler, so publishing from there would re-enter the
        modem mid-command. `tick()` answers on the next pass instead — a few tens of
        milliseconds later, and safe to be late, because a `query` deliberately does not
        arm the backend's confirmation deadline the way a `command` does.

        A query for a point this node does not report is ignored rather than answered:
        replying about a point we cannot see is exactly the fabricated reading the
        installed allow-list exists to prevent.
        """
        entry = self._points.get(point_id)
        if entry is None:
            return False
        if entry[0] is None:
            return False  # never read; nothing honest to say yet
        entry[3] = True
        return True

    # -- the re-assert ---------------------------------------------------

    def tick(self, now_ms):
        """Re-assert anything that has gone quiet, answer queries, retry failures.

        Call every loop. Returns the number of messages published, which is useful for a
        heartbeat log and for the bring-up check that counts re-asserts.

        Both `sensor/*/reading` and `point/*/reading` carry the same 30 s obligation.
        They reach it for different reasons — a sensor's retained copy is a bootstrap for
        a value about to be reconfirmed, while a point is never retained and the
        re-assert is the *only* thing that says its controller is alive — but the rule
        here is one rule, so it is one loop.
        """
        published = 0

        for sensor_id in sorted(self._sensors):
            state, last_ms, pending = self._sensors[sensor_id]
            if state is None:
                continue  # never observed; nothing to assert
            if not pending and _elapsed(now_ms, last_ms) < self._reassert_interval_ms:
                continue
            if self._publish(sensor_id, state, now_ms):
                self._sensors[sensor_id][1] = now_ms
                self._sensors[sensor_id][2] = False
                published += 1
            else:
                self._sensors[sensor_id][2] = True

        for point_id in sorted(self._points):
            position, last_ms, pending, queried = self._points[point_id]
            if position is None:
                continue  # never read; nothing to assert
            if (not pending and not queried
                    and _elapsed(now_ms, last_ms) < self._reassert_interval_ms):
                continue
            if self._publish_point(point_id, position, now_ms):
                self._points[point_id][1] = now_ms
                self._points[point_id][2] = False
                self._points[point_id][3] = False
                published += 1
            else:
                self._points[point_id][2] = True

        return published

    def _publish(self, sensor_id, occupied, now_ms):
        topic = self.sensor_topic(sensor_id)
        payload = self.reading_payload(occupied)
        try:
            ok = self._client.publish(topic, payload, qos=SENSOR_QOS, retain=SENSOR_RETAIN)
        except Exception as exc:
            ok = False
            self._report_failure(sensor_id, exc)
        else:
            if not ok:
                self._report_failure(sensor_id, None)
        return bool(ok)

    def _publish_point(self, point_id, position, now_ms):
        topic = self.point_topic(point_id)
        payload = self.point_reading_payload(point_id, position)
        try:
            ok = self._client.publish(topic, payload, qos=POINT_QOS, retain=POINT_RETAIN)
        except Exception as exc:
            ok = False
            self._report_failure(point_id, exc)
        else:
            if not ok:
                self._report_failure(point_id, None)
        return bool(ok)

    def _report_failure(self, sensor_id, exc):
        if self._on_publish_failure is None:
            return
        try:
            self._on_publish_failure(sensor_id, exc)
        except Exception:
            pass


def _elapsed(now_ms, then_ms):
    """Milliseconds between two ticks, tolerating MicroPython's wraparound.

    `utime.ticks_ms()` wraps at a platform-dependent bound, so plain subtraction can go
    negative and make an overdue sensor look freshly published — which would silently
    stop re-asserting it until the counter came round again. A negative difference means
    the counter wrapped; treat it as overdue.
    """
    delta = now_ms - then_ms
    return delta if delta >= 0 else 0x7FFFFFFF


def validate_sensor_id(sensor_id, what="sensor id"):
    """Refuse anything that is obviously not a real contract id.

    Cheap, and it catches the mistake that is otherwise invisible: a placeholder left in
    `config.py` publishes to a topic nobody subscribes to, and the node looks perfectly
    healthy while the orchestrator sees nothing at all.
    """
    if not isinstance(sensor_id, str) or not sensor_id:
        raise ValueError("%s must be a non-empty string, got %r" % (what, sensor_id))
    for marker in _PLACEHOLDER_MARKERS:
        if marker in sensor_id:
            raise ValueError(
                "%s %r looks like a placeholder — set the real value from the "
                "orchestrator's Configure > Sensors" % (what, sensor_id))
    for bad in ("+", "#", " "):
        if bad in sensor_id:
            raise ValueError(
                "%s %r contains %r, which would escape its topic namespace"
                % (what, sensor_id, bad))
    return sensor_id
