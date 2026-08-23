---
name: deploy
description: Copy a MicroPython node's code onto its Pico on the bench machine and confirm it came up. Use when asked to deploy, flash, push, or install code onto the io-node or rfid-node Pico, to check what is running on a board, or to open a REPL on one.
---

# Deploying a node to its Pico

**The Pico is not plugged into this Windows machine.** It lives on the bench box, so every
deploy is: copy the files to the bench, then drive `mpremote` *there* over USB. There is no
COM port here to shortcut through — verified 2026-08-23, this machine has only a legacy
`COM1`.

## The bench

`pbarrett@172.18.10.240`, Linux Mint 22.3. Key-based SSH, passwordless sudo, and the login
shell is **fish** — so every remote command needs `ssh host 'bash -lc "…"'`. POSIX syntax sent
bare is a parse error, not a failure you will recognise.

`pbarrett` is in `dialout`, so serial access needs no `sudo`.

**Address boards by their `by-id` path, never `ttyACM*`.** The numbers move between reboots
and there are three USB serial devices on that box, two of which belong to other projects:

| `by-id` | Device | Whose |
|---|---|---|
| `usb-MicroPython_Board_in_FS_mode_0d9a62134acbf42d-if00` | `ttyACM0` | **This repo's Pico** |
| `usb-Raspberry_Pi_Debugprobe_on_Pico__CMSIS-DAP__E66164084316322A-if01` | `ttyACM1` | PicoDCC's debug probe — **not ours** |
| `usb-METACHIP_BS100U_FTQC600L-if00-port0` | `ttyUSB0` | Not ours |

Flashing PicoDCC's board from this repo would be a serious mistake. Confirm the `by-id` path
before writing anything.

Only one MicroPython board is currently attached. When the second node arrives it gets its own
`by-id` string — add it to the table rather than guessing which is which.

## Before deploying: is anyone driving?

A deploy **resets the board**, and this board is a sensor publisher. While it is down its
readings go stale, and past `SENSOR_FRESHNESS_TIMEOUT_MS` the orchestrator marks the sensor
untrusted and degrades every block it feeds to `unknown` — which the fail-safe rule treats as
**occupied**. If a train is running under automation, that is a Safe-Stop.

So unless the user has just asked for it in the same breath, check and say what you find:

```bash
curl -s http://172.18.10.240:3000/health
```

`{"status":"online","reason":null}` and nobody at the controls — go ahead. A non-null `reason`,
or a train moving, is a question for the user, not a thing to deploy through.

## The deploy

```bash
bash scripts/deploy.sh io-node          # or rfid-node
bash scripts/deploy.sh io-node --dry-run
```

That is the whole task. The script stages `src/lib/` and `src/apps/<node>/` on the bench with
`scp` (Git Bash on Windows has no `rsync`), then runs `mpremote` there to copy `lib/` to the
board's `/lib` and the app's `main.py` and `config.py` to `/`, and resets. It refuses to deploy
past a red host suite, because the suite validates the very config being deployed.

Do not drive `mpremote` or `scp` by hand when the script exists. Reaching the same answer
manually costs a great deal of context and gets the `by-id` path wrong.

## Two things that will catch you out

**Any `mpremote` command stops the running node.** It interrupts whatever is executing and
drops the board into the raw REPL, so a "harmless" `fs ls` silently takes the node off the air
until the next `reset`. If you inspect a board mid-session, `mpremote ... reset` afterwards or
it stays down — and the orchestrator will degrade its blocks to `unknown` about 90 seconds
later.

**The filesystem root shadows `/lib`.** `sys.path` is `['', '.frozen', '/lib']`, so a stale
module left at the root wins over the one just deployed, and the board silently runs old code.
`deploy.sh` clears root `.py` files it does not own before copying, which is why the first real
deploy removed fourteen leftovers from the pre-restructure layout.

## Reading the board directly

These do not change anything, but they **do** stop the running node — see above:

```bash
ssh pbarrett@172.18.10.240 'bash -lc "mpremote connect id:0d9a62134acbf42d fs ls"'
ssh pbarrett@172.18.10.240 'bash -lc "mpremote connect id:0d9a62134acbf42d fs ls :/lib"'
```

`mpremote repl` is interactive and **will hang this session** — never run it. If you need REPL
output, ask the user to run it themselves, or use `mpremote exec` for a single statement.

To see what the node is actually publishing, watch the broker rather than the board:

```bash
ssh pbarrett@172.18.10.240 'bash -lc "mosquitto_sub -h localhost -t \"layout/#\" -v -W 10"'
```

## One-time provisioning

Neither tool is installed on the bench yet (verified 2026-08-23). Both need the user's
approval — they change the box:

```bash
ssh pbarrett@172.18.10.240 'bash -lc "sudo apt install -y pipx mosquitto-clients"'
ssh pbarrett@172.18.10.240 'bash -lc "pipx install mpremote && pipx ensurepath"'
```

`python3-mpremote` is **not** in Mint 22.3's archive, and Ubuntu 24.04 bases are PEP 668
managed, so `pip install` into the system Python is refused — hence `pipx`. If a script fails
because `mpremote` is missing, report that as the reason rather than as a deploy failure.

## Verifying

After a deploy, confirm the node is publishing again — do not assume the reset worked:

```bash
ssh pbarrett@172.18.10.240 'bash -lc "mosquitto_sub -h localhost -t \"layout/#\" -v -W 15"'
curl -s http://172.18.10.240:3000/health
```

A node that comes up but cannot reach the broker fails **silently**: the ESP-AT transport has
no reconnect, so a failed publish disappears and the board keeps looping. Silence on the broker
after a deploy is a failure, not a quiet success.

## Report

Which node, which `by-id` board, what was copied, whether the board reset cleanly, and what
was seen on the broker afterwards. If you skipped the "is anyone driving" check because the
user asked for the deploy directly, say so.
