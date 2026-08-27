# Node startup, and what the LED is telling you

## The fault this is about

The IO node did not always come up after a power cycle. When it failed it failed
completely and silently: the onboard LED never lit, nothing appeared on the broker, and
the board sat at a REPL until someone power-cycled it again. The orchestrator behaved
correctly — the Goods Shed sensors went stale, the block degraded to `unknown`, and the
fail-safe rule treated that as occupied — but nothing anywhere pointed at the node.

That is the failure this repo rates worst. The node stopped reporting and said nothing.

## Root cause: the Pico wins the power-on race

The Pico and the ESP-AT modem come out of a power cycle together, and the Pico is much
faster. Measured on the bench, 2026-08-27:

| Event | Time from power-on |
|---|---|
| Pico reaches its first AT command | ~300 ms (MicroPython's boot, plus **19 ms** of I2C and expander setup) |
| Modem reaches `ready` | **1.2 s – 3.5 s** |

Both ends of the modem's spread were measured in the same session: one reset reached
`ready` at 3471 ms, another at 1232 ms. The variance is Ethernet link negotiation —
`+ETH_CONNECTED` came at 3460 ms and 1221 ms respectively.

`MQTTATClient.setup()` opened by sending `AT+RST` and waiting on the constructor's
2000 ms default. So the deadline for a reply fell about 2.3 s after power-on, while the
modem became ready somewhere between 1.2 s and 3.5 s. **Sometimes it made it, sometimes
it did not** — which is exactly the reported "doesn't always start".

A command sent into that window is not refused. It is swallowed whole: no echo, no
reply. Reproduced deterministically by commanding the modem mid-boot:

```
STEP 3 - the cold-boot race: command the modem while it is still booting.
  modem is now rebooting; waiting 200 ms then sending AT+RST as setup() does
  -> OK within setup()'s 2000 ms default? NO -> RuntimeError('AT+RST failed')
```

The exception propagated out of `main()`, which was called bare at module scope, so
`main.py` simply ended.

## Fix 1: ask before commanding

`MQTTATClient.wait_for_modem()` polls a bare `AT` until the modem answers, for up to
15 s, before anything else is sent. Only then does `AT+RST` go out. A modem that never
answers now raises `ModemNotResponding` rather than looking like a failed reset.

A related blind spot was closed at the same time: `wait_for_line()` only inspects lines
recorded *after* it is called, so an `+ETH_GOT_IP` arriving in the same read as the
reset's own `OK` would have been missed, and `setup()` would have sat out its full 30 s
and then raised as though the network were down. `setup()` now checks what has already
arrived before it starts waiting.

## Fix 2: a node that cannot start must not fall silent

`node_startup.start_supervised()` retries startup three times — flashing for 2 s, 5 s
and 10 s between attempts — and then resets the board, which starts the cycle again.
It never returns without a running node.

The first retry alone covers the fault above: the modem needs at most 3.5 s from cold
and `wait_for_modem()` now waits for it, so anything still failing on the second attempt
is a real fault rather than the power-on race.

A crash in the *main loop* is treated the same way, for the same reason — it is equally
a node that stopped reporting without saying so.

Retrying a wiring fault forever is deliberate. An expander missing at boot is usually a
loose ribbon cable, not a permanent fact, and a board that keeps trying and keeps
flashing is easier to find than one that is simply dark.

## The flash codes

| LED | Meaning |
|---|---|
| **Solid on** | Running: sensors configured, broker link up |
| **Off** | No power, or a hard crash that took the board out |
| **N flashes, pause, repeat** | Startup failed at stage N — retrying, then resetting |

| Code | Stage | What it means | Where to look |
|---|---|---|---|
| **2** | Sensors | A configured expander is not on the I2C bus | Ribbon to boards 1/2, `config.py` addresses |
| **3** | Modem | The ESP-AT modem is not answering AT commands | UART wiring GP8/GP9, modem power |
| **4** | Network | The modem answers, but never reported an IP | Ethernet cable, DHCP, switch port |
| **5** | Broker | Network is up, the MQTT link could not be made | Mosquitto on the bench box, `MQTT_HOST` |
| **6** | Runtime | The node came up, then the main loop crashed | USB console — this one is a bug, not wiring |
| **7** | Unknown | Something else | USB console |

Codes start at 2 on purpose. A single flash is too easily confused with a board merely
blinking on boot, and a miscounted code is worse than no code.

The pattern is 200 ms on, 250 ms between flashes, and a 1400 ms gap before the cycle
repeats. The gap is deliberately much longer than the space between flashes — otherwise
a 2 reads as a 4. A cycle is always completed once started, so a code is never cut off
half way through.

## Reading a code

Count the flashes between the long gaps. Then plug the USB lead in and look at the
console — every failure prints the exception and the code it chose before it flashes.

## What is covered by the host suite, and what is not

`tests/test_mqtt_setup.py`, `tests/test_node_startup.py` and `tests/test_status_led.py`
prove the probe, the retry-then-reset policy, the code mapping, and the flash pattern
against a scripted modem, with no hardware attached.

What the host suite cannot prove is that the LED pin actually lights and that the
modem's real boot timing stays inside the 15 s budget. Both were checked on the bench —
see the timings above, and `scripts/` has no permanent home for the diagnostic, which
lived in the scratchpad.
