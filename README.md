# layout-feedback

MicroPython firmware for the Raspberry Pi Pico nodes that connect layout hardware — block
detectors, IO expanders, RFID readers — to MQTT, for the Westgate Hollow model railway.

Part of a four-repo control stack:
[layout-orchestration](https://github.com/bazauto/layout-orchestration) (backend and operator
UI), [PicoDCC](https://github.com/bazauto/PicoDCC) (DCC command station),
[esp-layout-controller](https://github.com/bazauto/esp-layout-controller) (touchscreen
throttle), and this.

The orchestrator's `docs/mqtt-contract.md` is **binding** on what these nodes publish. It is
owned there, not here.

## Layout

```
src/lib/     deployed to the board's /lib, which MicroPython puts on sys.path
tools/       bench utilities, never deployed
tests/       host pytest — stubs `machine` and `utime`, needs no hardware
scripts/     run_tests.sh
docs/        pin allocation, hardware notes, plans
```

A node imports `mqtt_at`, not `lib.mqtt_at`, because `/lib` is on the path. Tests import
device modules by exactly the name the board uses.

`src/apps/io-node/` and `src/apps/rfid-node/` do not exist yet — see the issues below.
`main.py` is the pre-split combined app, kept running until they land.

## Hardware

| Device | Bus | Notes |
|---|---|---|
| MCP23017 ×2 | I2C0, SDA=GP4 SCL=GP5, `0x20`/`0x21` | 16 pins each. Board 1 current sensing, board 2 IR — see `docs/pin-allocation.md` |
| PCF8591 ADC | I2C0, `0x48` | 8-bit, 4 channels. Currently unused |
| PN7150 NFC | I2C1, SDA=GP2 SCL=GP3, `0x28` | NCI + IRQ. The IRQ means "a message is ready", not "a tag is present" |
| TCA9548A mux | I2C1, `0x70` | Fans I2C1 out to up to 8 readers |
| ESP-AT modem | UART1, TX=GP8 RX=GP9, 9600 | Wired ethernet; reports `+ETH_GOT_IP` when ready |
| Legacy Models LM-iD.1 | board 1 inputs | The current-sensing detectors. Output stage and its two modes: `docs/block-detector-wiring.md` |
| Waveshare IR reflective | board 2 inputs | LM393, run at 3.3 V. A newer part with none of the LM-iD's ambiguity |

## Testing

```bash
python -m pip install --user pytest    # once
python -m pytest                        # or: bash scripts/run_tests.sh
```

No hardware needed. `tests/conftest.py` installs stand-ins for `machine` and `utime` before
any device module is imported, including a clock that drives timers — so debounce and
timeout behaviour is tested by advancing time rather than by sleeping.

Paths come from `pytest.ini`; the old `PYTHONPATH=.` prefix is no longer needed.

## Deploying

The Pico lives on the bench machine, not on a dev machine. See `.claude/skills/deploy/SKILL.md`
for the whole procedure, including which `by-id` serial device belongs to this repo — two of
the three on that box belong to other projects.

## Status

Being restructured and brought onto the MQTT contract:

- [#1](https://github.com/bazauto/layout-feedback/issues/1) — restructure into two nodes
- [#2](https://github.com/bazauto/layout-feedback/issues/2) — meet the MQTT contract
- [#3](https://github.com/bazauto/layout-feedback/issues/3) — end-to-end bring-up, one
  current-sensing and one IR sensor
- [#4](https://github.com/bazauto/layout-feedback/issues/4) — RFID restructure only, pending
  hardware and `layout-orchestration#39`
- [#9](https://github.com/bazauto/layout-feedback/issues/9) — a broken sensor wire reads as
  clear track. Reopened: the closed-circuit fix it rejected turns out to be available
  (`docs/block-detector-wiring.md`)

Plan: `docs/plans/2026-08-23-node-split.md`. Working agreement: `CLAUDE.md`.
