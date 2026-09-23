"""The node's broker identity, read from a file that `deploy.sh` puts on the board.

The broker refuses anonymous clients (#7), so a node needs a username and password. The
password is not in git: its only copy lives on the bench box, in
`~/.config/layout-feedback/<node>.json`, and every deploy copies it onto the board as
`/mqtt_credentials.json`. See `docs/broker-auth.md`.

A missing or malformed file fails startup with its own LED code. It never falls back to
connecting anonymously — that would be refused anyway, and would flash as a broker fault,
sending whoever is looking to the bench box instead of to the deploy.

Pure — no `machine` import — so every check here runs in the host suite.
"""

import json


# ESP-AT caps `AT+MQTTUSERCFG`'s username and password at 64 bytes each. Checked here so
# an over-long value fails as a credentials fault, not as a broker that refuses us.
MAX_FIELD_LENGTH = 64

# `"`, `\` and `,` all have meaning inside an AT command's quoted argument. Generated
# passwords never need them, so refuse them rather than trust an escaping rule for a
# field whose only failure symptom is "connection refused".
_FORBIDDEN = '"\\,'


class CredentialsError(RuntimeError):
    """The board has no usable broker credentials. Redeploy the node to fix it."""


def parse_credentials(text):
    """Return `(username, password)` from the file's JSON text, or raise."""
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise CredentialsError("credentials file is not valid JSON (%s)" % exc)

    if not isinstance(data, dict):
        raise CredentialsError("credentials file must hold a JSON object")

    fields = []
    for key in ("username", "password"):
        value = data.get(key)
        if not isinstance(value, str) or not value:
            raise CredentialsError("credentials file has no %s" % key)
        if len(value) > MAX_FIELD_LENGTH:
            raise CredentialsError(
                "%s is %d characters; the modem accepts at most %d"
                % (key, len(value), MAX_FIELD_LENGTH))
        for ch in value:
            if ch in _FORBIDDEN or not (" " < ch <= "~"):
                # Never echo the value itself: this goes to the console.
                raise CredentialsError(
                    "%s contains a character the modem's AT syntax cannot carry" % key)
        fields.append(value)

    return fields[0], fields[1]


def load_credentials(path, open_fn=open):
    """Read and validate the credentials file at `path`."""
    try:
        with open_fn(path) as f:
            text = f.read()
    except OSError:
        raise CredentialsError(
            "no credentials at %s — deploy the node with scripts/deploy.sh" % path)
    return parse_credentials(text)


__all__ = [
    "CredentialsError", "MAX_FIELD_LENGTH", "load_credentials", "parse_credentials",
]
