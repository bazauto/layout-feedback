# layout-feedback

MicroPython firmware for the Raspberry Pi Pico nodes that connect layout hardware — block
detectors, IO expanders, RFID readers — to MQTT, for the Westgate Hollow model railway.

Part of the Westgate Hollow control stack:
[layout-orchestration](https://github.com/bazauto/layout-orchestration) (backend and operator
UI), [PicoDCC](https://github.com/bazauto/PicoDCC) (DCC command station),
[esp-layout-controller](https://github.com/bazauto/esp-layout-controller) (touchscreen
throttle), and this. [block-detection](https://github.com/bazauto/block-detection) is the
detector PCB this firmware reads.

The orchestrator's `docs/mqtt-contract.md` is **binding** on what these nodes publish. It is
owned there, not here.

## Layout

```
src/lib/            deployed to the board's /lib, which MicroPython puts on sys.path
src/apps/io-node/   deployed to the board's root: main.py and config.py
tools/              bench utilities, never deployed
tests/              host pytest — stubs `machine` and `utime`, needs no hardware
scripts/            deploy.sh, run_tests.sh
docs/               wiring, pin allocation, startup and LED codes, broker auth, plans
```

A node imports `mqtt_at`, not `lib.mqtt_at`, because `/lib` is on the path. Tests import
device modules by exactly the name the board uses.

Each node keeps all of its wiring and identity in its own `config.py`. Nothing under
`src/lib/` hardcodes a pin, an address or a layout id. `src/apps/rfid-node/` does not
exist yet; see [#4](https://github.com/bazauto/layout-feedback/issues/4).

## Hardware

| Device | Bus | Notes |
|---|---|---|
| MCP23017 ×3 | I2C0, SDA=GP4 SCL=GP5, `0x20`/`0x21`/`0x22` | 16 pins each. Board 1 current sensing, board 2 IR, board 3 point feedback. See `docs/pin-allocation.md` |
| PCF8591 ADC | I2C0, `0x48` | 8-bit, 4 channels. Currently unused |
| PN7150 NFC | I2C1, SDA=GP2 SCL=GP3, `0x28` | NCI + IRQ. The IRQ means "a message is ready", not "a tag is present" |
| TCA9548A mux | I2C1, `0x70` | Fans I2C1 out to up to 8 readers |
| ESP-AT modem | UART1, TX=GP8 RX=GP9, 9600 | Wired ethernet; reports `+ETH_GOT_IP` when ready |
| Legacy Models LM-iD.1 | board 1 inputs | The current-sensing detectors, active low. Output stage and its two modes: `docs/block-detector-wiring.md` |
| Waveshare IR reflective | board 2 inputs | LM393, run at 3.3 V, active low |
| Cobalt iP Digital points | board 3 (`0x22`), planned | Commanded over DCC, never MQTT, and can report nothing itself. Neither changeover is free (`S2` powers the frog), so the feedback source is undecided. See `docs/point-position-feedback.md` |

Polarity is set per sensor in `config.py`, because it is a property of the device and not of
the board. The `block-detection` board is active high, so a broken wire reads occupied.

## Testing

```bash
python -m pip install --user pytest    # once
python -m pytest                        # or: bash scripts/run_tests.sh
```

No hardware needed. `tests/conftest.py` installs stand-ins for `machine` and `utime` before
any device module is imported, including a clock that drives timers. So debounce and
timeout behaviour is tested by advancing time, not by sleeping.

## Deploying

```bash
bash scripts/deploy.sh io-node
```

The Pico is plugged into the bench machine, not a dev machine, so the script stages the files
there and drives `mpremote` over USB. It refuses to deploy if the host suite is red.
`.claude/skills/deploy/SKILL.md` has the whole procedure. That includes which `by-id` serial
device belongs to this repo: two of the three on that box belong to other projects.

The broker refuses anonymous clients. A node's MQTT password lives only on the bench and is
copied onto the board on every deploy, so it is never committed. See `docs/broker-auth.md`.

A node that cannot start flashes its failure stage on the onboard LED and retries. Solid
means it is running. The codes are in `docs/startup-and-status-led.md`.

## Status

The IO node is deployed and working end to end. Both Goods Shed sensors, one current-sensing
and one IR, publish contract readings that the orchestrator trusts.

Still open:

- [#9](https://github.com/bazauto/layout-feedback/issues/9): a broken sensor wire reads as
  clear track. The current-sensing fix is the `block-detection` board, and it is under test.
  IR is still open.
- [#10](https://github.com/bazauto/layout-feedback/issues/10): the Goods Shed IR beam
  oscillates with a stationary loco over it.
- Point feedback on board 3 is built (#15), but it publishes nothing until a feedback source
  is chosen. See `docs/point-position-feedback.md`.
- [#25](https://github.com/bazauto/layout-feedback/issues/25): TLS on the broker.
- [#4](https://github.com/bazauto/layout-feedback/issues/4): RFID. Restructure only, pending
  hardware and `layout-orchestration#39`.

Working agreement: `CLAUDE.md`.

## Licence

Released under the MIT Licence — see [`LICENSE`](LICENSE).

Everything in this repository is first-party. The device drivers under `src/lib/`
(`mcp23017_io.py`, `pcf8591_adc.py`, `pn7150.py`) were written against the manufacturers'
datasheets rather than adapted from existing libraries, so no third-party code is vendored or
redistributed here.

The nodes run on [MicroPython](https://micropython.org/) (MIT), which is flashed to the board
separately and is not distributed with this repository. `pytest` (MIT) is a host-side test
dependency only.

Manufacturer datasheets are deliberately **not** committed — they are copyrighted and their
terms generally forbid redistribution. Links instead:
[MCP23017](https://www.microchip.com/en-us/product/MCP23017),
[PCF8591](https://www.nxp.com/products/PCF8591),
[PN7150](https://www.nxp.com/products/PN7150).
