# Broker authentication and ACL

Issue #7. The bench broker used to take anonymous clients on the LAN, so any device on
that network could publish a fabricated `clear` on an occupied block, and the
orchestrator would believe it. Once contract control topics carry commands, the same gap
means throttle commands. Now every client has an identity, and each identity is limited
to the topics it owns.

**The ACL matters more than the password.** A password alone only proves that a client is
*some* layout client. The ACL is what stops a compromised sensor node from publishing
`point/*/command` or `loco/*/command`.

## Identities

| User | Who | May |
|---|---|---|
| `orchestrator` | The backend, over `mqtt://localhost:1883` | read and write `layout/#` |
| `io-node` | This repo's IO node | **write** `sensor/+/reading` and `point/+/reading`; **read** `point/+/query` |
| `monitor` | People running `mosquitto_sub`, on the bench or from the LAN | read `layout/#` and `$SYS/#` |

Anonymous clients are refused outright (`allow_anonymous false`).

The `io-node` rules are pinned to this layout's id and scoped to topic *kinds*, not to
individual sensor ids. That choice is deliberate. Installing a sensor must never need a
broker change, because of the failure described next.

## A denied publish is silent

Under MQTT 3.1.1, mosquitto drops a publish that the ACL denies, and it still acknowledges
that publish to the client. `publish()` returns True and the node believes it is
reporting, but nobody hears it. That is exactly the failure rule 3 in `CLAUDE.md` exists
to prevent, and the node cannot detect it. The only trace is on the broker:

```
ssh pbarrett@172.18.10.240 'bash -lc "sudo grep Denied /var/log/mosquitto/mosquitto.log | tail"'
```

When a node is up with a solid LED but a topic is missing, look there first. A
contract change that adds a topic this node publishes needs this ACL updated in the
same change.

## Where the secrets live

**Nothing secret is in git.** Everything below is on the bench box, `172.18.10.240`:

| File | Holds | Mode |
|---|---|---|
| `/etc/mosquitto/passwd` | Hashed passwords for all three users | `0600 mosquitto` |
| `/etc/mosquitto/acl` | The ACL, as in the table above | `0600 mosquitto` |
| `/etc/mosquitto/conf.d/lan.conf` | The listener; turns authentication on | `0644 root` |
| `~/.config/layout-feedback/io-node.json` | The node's plaintext username and password | `0600 pbarrett` |
| `~/.config/mosquitto_sub` | `-u monitor -P …`, the default options for `mosquitto_sub` | `0600 pbarrett` |
| `/opt/layout-orchestrator/.env` | `MQTT_USERNAME` / `MQTT_PASSWORD` | `0600 pbarrett` |

`mosquitto_sub` reads `~/.config/mosquitto_sub` automatically. That is why every
`mosquitto_sub -h localhost …` command in this repo and in the deploy skill keeps working
unchanged. `mosquitto_pub` has no default identity. Hand-publishing, for example
clearing a retained reading, now takes explicit credentials.

The broker config:

```
# /etc/mosquitto/conf.d/lan.conf
listener 1883
allow_anonymous false
password_file /etc/mosquitto/passwd
acl_file /etc/mosquitto/acl
```

The notes from #7 about the listener still hold. Leave the bind address off, and
remember that the orchestrator reaches the broker over loopback through this same
listener.

## How the Pico gets its password

1. `scripts/deploy.sh` checks that `~/.config/layout-feedback/<node>.json` exists on the
   bench **before touching the board**, and refuses to deploy if it is missing.
2. It copies that file directly from the bench's own copy to the board as
   `/mqtt_credentials.json`, so the password never reaches the dev machine or git.
   Every deploy copies it again, so rotating the password is: change it on the bench,
   then redeploy.
3. At startup `broker_credentials.load_credentials()` reads the file and validates it,
   and `MQTTATClient` sends the credentials in `AT+MQTTUSERCFG`.

Details that matter:

- **Not a `.py` file.** The deploy's root sweep deletes every `.py` at the root except
  `main.py` and `config.py`. A `.json` survives the sweep, and nothing can import it or
  shadow it.
- **No anonymous fallback.** A missing or malformed file raises `CredentialsError`, and
  the node flashes **code 8**, not code 5. That sends you to the deploy rather than to
  the broker. A password the broker *rejects* still flashes code 5, because the modem
  reports a refused connect the same way as an unreachable one. Check the broker log
  for `not authorised`.
- **Only characters AT can carry.** Values are limited to 64 printable ASCII characters,
  with no `"`, `\` or `,`. The passwords are 32 random alphanumerics, so they fit.
- **Redacted on the console.** The modem echoes `AT+MQTTUSERCFG` back to the Pico, so
  `MQTTATClient` strips the password from its debug log and failure dumps.
- The password sits in plain text on the Pico's flash. Anyone with the board and a USB
  lead can read it. The ACL is what limits the damage: the most that stolen password
  can do is publish readings.

## Rotating a password, or adding a node

On the bench:

```bash
sudo mosquitto_passwd /etc/mosquitto/passwd io-node     # prompts; never pass -b
# edit ~/.config/layout-feedback/io-node.json to match
sudo systemctl reload mosquitto                          # SIGHUP rereads passwd and acl
```

Then run `bash scripts/deploy.sh io-node`. A new node gets its own user, its own ACL
block and its own `<node>.json`. **Don't share `io-node` between boards.** A shared
identity can't be revoked without taking both boards off the air.

## Verifying

- An anonymous client is refused: `mosquitto_sub -h 172.18.10.240 -t '#' -u '' -W 3`
  from another machine exits with `Connection Refused: not authorised`.
- `monitor` works from the LAN: `mosquitto_sub -h 172.18.10.240 -u monitor -P … -t 'layout/#' -v`.
- The node's readings arrive every 25 s, and the orchestrator shows its blocks trusted.
- `io-node` can't command. Publish to `point/+/command` with its credentials, and the
  broker logs `Denied PUBLISH from layout-feedback-io-node`.
