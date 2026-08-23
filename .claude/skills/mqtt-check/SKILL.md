---
name: mqtt-check
description: Validate this repo's MQTT publish/subscribe code against the orchestrator's binding contract — topic shape, payload schema, QoS, retention, and the 30-second re-assert. Use before or after writing any code that publishes, subscribes, or parses an MQTT message here, and when reviewing a diff that touches the transport or a node's publish path.
---

# MQTT contract check

`../layout-orchestration/docs/mqtt-contract.md` is **binding on this repo**. The orchestrator
is built against it, so drift here is a field bug on a live layout, not a refactor. This repo
does not own the contract and must never amend it — a contract change is a design decision
made in `bazauto/layout-orchestration`.

**Read the contract in full before checking anything.** Do not work from memory of it, and do
not work from this file's summary of it — this file goes stale, the contract does not.

## Checklist

Run every item against the code under review. Report pass/fail per item with a `file:line`
reference for each failure.

**Topic**
1. Prefixed `layout/{layoutId}/` — no unscoped topics. This repo's history is full of
   `track/sensor/*`, which is **not** a contract topic.
2. The segment structure matches a row in the contract's Topic Reference table exactly.
3. `layoutId` comes from the node's `config.py`, never hardcoded in a module under `lib/`.
4. `sensorId` matches the sensor's configured identifier in the layout database — a
   layout-level name, not a wiring-derived one. `mcp20_in0` names a chip and a pin; it does
   not name anything the orchestrator knows about.

**Direction**
5. The node publishes only on topics listed as inbound-to-backend for a sensor device, and
   subscribes only to topics the contract sends outward. A node must never subscribe to its
   own broadcasts.

**Payload**
6. UTF-8 JSON. Every field in the contract's schema table present and correctly typed.
7. No extra fields. Adding one requires the contract to be amended first, upstream.
8. `state` uses the contract's vocabulary (`"occupied"` / `"clear"`) — not `ACTIVE` /
   `INACTIVE`, not `1` / `0`.

**QoS, retention and liveness** — the safety-critical section
9. QoS matches the table.
10. `sensor/*/reading` is published **retained**.
11. **Every sensor reading is re-asserted at least every 30 seconds**, unchanged, whether or
    not it changed. This is a hard requirement. A sensor from which the backend has received
    no *live* message inside `SENSOR_FRESHNESS_TIMEOUT_MS` (default 90 s) is untrusted, and
    every block it feeds degrades to `unknown` — which the fail-safe rule treats as occupied.
    Publishing on change only is the single highest-severity failure in this checklist.
12. `system/status` is registered as the MQTT Last Will and Testament with the `"offline"`
    payload, so a dead node announces itself rather than going quiet.
13. Any control topic the node publishes is **not** retained. A retained command replays to
    every future subscriber and causes movement with no operator present.

**Transport correctness** — specific to the ESP-AT path here
14. `+MQTTSUBRECV` parsing is **length-aware**. Splitting on CRLF and ignoring the declared
    length works for `ACTIVE`/`INACTIVE` and breaks on JSON: a payload containing `\r\n`
    arrives as two lines, and a payload containing `"` truncates.
15. Outbound JSON is escaped for embedding in an `AT+MQTTPUB` quoted argument, or sent via
    `AT+MQTTPUBRAW`. Quotes and backslashes in a payload must not be able to terminate the AT
    command early.
16. The return value of every publish and subscribe is **checked**. A discarded `ok` means a
    failed publish vanishes silently, and the node goes on believing it is reporting.
17. There is a reconnect path. If the modem loses the broker, the node must notice rather than
    publishing into the void until someone power-cycles it.

**Layering**
18. No business logic inside a message callback — parse, validate, delegate.
19. Topic and payload construction live in a testable module with no `machine` import, so the
    host suite can cover them.

## Output

A short table: item, pass/fail, `file:line`. Then the failures in severity order — missing
re-assert first, then retention violations, then parsing correctness, then schema drift, then
style.

If the code needs a topic or field the contract does not define — **RFID tag reads are the
known case; the contract has no tag-reader topic class at all** — do not invent one silently.
State that the contract must be amended upstream, propose the exact table row and schema block
for `docs/mqtt-contract.md`, and say that `bazauto/layout-orchestration` needs the matching
change before this repo can publish it.
