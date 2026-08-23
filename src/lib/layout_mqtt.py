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

    # -- topics ----------------------------------------------------------

    @property
    def topic_base(self):
        return "layout/%s" % self._layout_id

    def sensor_topic(self, sensor_id):
        return "%s/sensor/%s/reading" % (self.topic_base, sensor_id)

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

    def tick(self, now_ms):
        """Re-assert anything that has gone quiet, and retry anything that failed.

        Call every loop. Returns the number of sensors published, which is useful for a
        heartbeat log and for the bring-up check that counts re-asserts.
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
