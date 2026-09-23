"""Resolve a node's sensor allocation table against its installed allow-list.

Pure — no `machine` import — so the checks below run in the host suite. They all fail at
startup rather than at runtime, because every mistake they catch is otherwise invisible:
the node comes up, looks healthy, and publishes something wrong or nothing at all.
"""

from layout_mqtt import validate_sensor_id


def select_installed(sensors, installed):
    """Return the allocation entries for `installed`, in that order.

    `sensors` is the whole allocation table; `installed` names what is physically wired.
    Only the returned entries should ever be published — an unwired MCP23017 input sits
    pulled up, reads as inactive, and would publish `clear` for a block nothing is
    watching.

    Raises `ValueError` on anything that would produce a wrong or silent node.
    """
    by_id = {}
    by_position = {}

    for entry in sensors:
        try:
            sensor_id = entry["sensor_id"]
            expander = entry["expander"]
            pin = entry["pin"]
            active_low = entry["active_low"]
        except (KeyError, TypeError):
            raise ValueError(
                "every SENSORS entry needs sensor_id, expander, pin and active_low — got %r"
                % (entry,))

        validate_sensor_id(sensor_id)

        # No default, and nothing truthy standing in for a bool. Polarity is a property of
        # the device on the far end of the wire, and a guessed one publishes `clear` for
        # every occupied block that sensor watches.
        if active_low is not True and active_low is not False:
            raise ValueError(
                "%s: active_low must be True or False — got %r" % (sensor_id, active_low))

        if sensor_id in by_id:
            raise ValueError("sensor id %r appears twice in SENSORS" % sensor_id)
        by_id[sensor_id] = entry

        if not 0 <= pin <= 15:
            raise ValueError(
                "%s: pin %r is out of range — an MCP23017 has pins 0-15" % (sensor_id, pin))

        # Two sensors sharing a pin means at least one of them is reporting another
        # block's state, which is undetectable from the broker.
        position = (expander, pin)
        if position in by_position:
            raise ValueError(
                "%s and %s are both on expander 0x%02X pin %d"
                % (by_position[position], sensor_id, expander, pin))
        by_position[position] = sensor_id

    selected = []
    seen = set()
    for sensor_id in installed:
        if sensor_id in seen:
            raise ValueError("%r is listed twice in INSTALLED" % sensor_id)
        seen.add(sensor_id)
        if sensor_id not in by_id:
            raise ValueError(
                "INSTALLED names %r, which is not in SENSORS — check it against the "
                "orchestrator's Configure > Sensors" % sensor_id)
        selected.append(by_id[sensor_id])

    return selected


def is_occupied(level, active_low):
    """Translate a raw pin level into contract occupancy.

    One line, and it has its own name because getting it backwards publishes `clear`
    for an occupied block — which for a block_detection sensor is enough on its own to
    let the orchestrator clear the block. `active_low` comes from the sensor's own
    SENSORS entry, because it is a property of the device on the far end of the wire:
    the LM-iD.1 detectors and the IR modules are active low, the bazauto/block-detection
    board is active high (see docs/pin-allocation.md).
    """
    return (level == 0) if active_low else (level == 1)
