"""Resolve a node's point allocation table, and decode a pair of S2 contacts.

Pure — no `machine` import — so every check below runs in the host suite. They all fail
at startup rather than at runtime, because a point mis-wired or mis-allocated produces a
node that looks perfectly healthy while reporting another motor's position.

The design and the hazards are in `docs/point-position-feedback.md`. The two facts that
shape this module:

- **A point is commanded and read by two devices that know nothing about each other.**
  The Cobalt iP takes DCC accessory commands and can report nothing; position comes only
  from its `S2` changeover wired into an MCP23017 here. So `points.dcc_address` in the
  orchestrator and `point_id` -> pins here are independent mappings that nothing
  cross-checks.
- **Two inputs per point, not one.** A single input infers `reverse` from the absence of
  `normal`, and an absence is equally a broken wire, a lost supply, or a point sitting
  mid-throw. Two inputs make `unknown` observable instead of assumed.
"""

from layout_mqtt import (
    POSITION_NORMAL, POSITION_REVERSE, POSITION_UNKNOWN, validate_sensor_id,
)


def select_installed_points(points, installed):
    """Return the allocation entries for `installed`, in that order.

    `points` is the whole allocation table; `installed` names what is physically wired.
    Only the returned entries should ever be published — an unwired pair reads both-open
    and would publish a confident `unknown` for a point nothing is watching, which is a
    reading the orchestrator will act on.

    Raises `ValueError` on anything that would produce a wrong or silent node.
    """
    by_id = {}
    by_position = {}

    for entry in points:
        try:
            point_id = entry["point_id"]
            expander = entry["expander"]
            normal_pin = entry["normal_pin"]
            reverse_pin = entry["reverse_pin"]
        except (KeyError, TypeError):
            raise ValueError(
                "every POINTS entry needs point_id, expander, normal_pin and "
                "reverse_pin — got %r" % (entry,))

        validate_sensor_id(point_id, what="point id")

        if point_id in by_id:
            raise ValueError("point id %r appears twice in POINTS" % point_id)
        by_id[point_id] = entry

        for name, pin in (("normal_pin", normal_pin), ("reverse_pin", reverse_pin)):
            if not 0 <= pin <= 15:
                raise ValueError(
                    "%s: %s %r is out of range — an MCP23017 has pins 0-15"
                    % (point_id, name, pin))

        # One input read twice is the one-input design this replaces, wearing the
        # two-input design's clothes: it would corroborate itself and always agree.
        if normal_pin == reverse_pin:
            raise ValueError(
                "%s: normal_pin and reverse_pin are both %d — a point needs two "
                "separate contacts to tell `unknown` from `reverse`"
                % (point_id, normal_pin))

        for name, pin in (("normal", normal_pin), ("reverse", reverse_pin)):
            position = (expander, pin)
            if position in by_position:
                raise ValueError(
                    "%s %s and %s are both on expander 0x%02X pin %d"
                    % (point_id, name, by_position[position], expander, pin))
            by_position[position] = "%s %s" % (point_id, name)

    selected = []
    seen = set()
    for point_id in installed:
        if point_id in seen:
            raise ValueError("%r is listed twice in POINTS_INSTALLED" % point_id)
        seen.add(point_id)
        if point_id not in by_id:
            raise ValueError(
                "POINTS_INSTALLED names %r, which is not in POINTS — check it against "
                "the orchestrator's points table" % point_id)
        selected.append(by_id[point_id])

    return selected


def assert_no_pin_overlap(sensor_entries, point_entries):
    """Refuse a pin claimed by both a sensor and a point.

    The two allocation tables are validated separately, so nothing else would notice.
    Both would configure the same expander pin, one of them would be reading the other's
    hardware, and both would look healthy on the broker.
    """
    claimed = {}

    for entry in sensor_entries:
        claimed[(entry["expander"], entry["pin"])] = "sensor %s" % entry["sensor_id"]

    for entry in point_entries:
        for name in ("normal_pin", "reverse_pin"):
            position = (entry["expander"], entry[name])
            owner = claimed.get(position)
            if owner is not None:
                raise ValueError(
                    "point %s %s and %s are both on expander 0x%02X pin %d"
                    % (entry["point_id"], name, owner, position[0], position[1]))
            claimed[position] = "point %s %s" % (entry["point_id"], name)


def is_closed(level, active_low):
    """Whether one throw of an `S2` changeover is closed.

    `S2-C` is wired to 0 V and each throw to its own expander input, so a closed contact
    pulls its input down and reads 0 against the internal pull-up. Its own flag rather
    than the sensors' `ACTIVE_LOW`: this is a third device that happens to agree with the
    other two, and the repo has already been bitten once by treating a coincidence as a
    rule.
    """
    return (level == 0) if active_low else (level == 1)


def decode_position(normal_closed, reverse_closed):
    """Turn a pair of contact states into a contract position.

    Returns `(position, cross_wired)`. `cross_wired` is True only for the electrically
    impossible both-closed case.

    | `normal` | `reverse` | Reading | Meaning |
    |---|---|---|---|
    | closed | open | `normal` | corroborated |
    | open | closed | `reverse` | corroborated |
    | open | open | `unknown` | mid-travel, broken wire, or lost supply |
    | closed | closed | `unknown` | impossible on an SPDT — cross-wiring |

    The both-open row is not a fault and must not be suppressed. A break-before-make
    changeover genuinely passes through it on every throw, so the honest sequence is
    `normal` -> `unknown` -> `reverse`. The backend expects it and its confirmation
    timeout is 8000 ms, so a normal throw lands well inside.

    The both-closed row cannot happen on an SPDT changeover, so seeing it means the
    wiring is not what this node thinks it is. It reads `unknown` — the safe answer —
    and is flagged separately so it can be reported rather than silently averaged away.
    """
    if normal_closed and reverse_closed:
        return POSITION_UNKNOWN, True
    if normal_closed:
        return POSITION_NORMAL, False
    if reverse_closed:
        return POSITION_REVERSE, False
    return POSITION_UNKNOWN, False


__all__ = [
    "select_installed_points", "assert_no_pin_overlap", "is_closed", "decode_position",
]
