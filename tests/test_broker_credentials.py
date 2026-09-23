"""The node's broker credentials, read from the file deploy.sh puts on the board (#7).

The failure these guard against is a node that cannot authenticate and says so as the
wrong thing. A missing file must flash as credentials, not as a broker fault, and a
value the modem cannot carry must be refused here rather than come back as "refused".
"""

import json

import pytest

from broker_credentials import (
    CredentialsError, MAX_FIELD_LENGTH, load_credentials, parse_credentials,
)
from node_startup import CODE_BROKER, CODE_CREDENTIALS, code_for_error


def creds(**fields):
    return json.dumps(fields)


def test_a_well_formed_file_gives_the_username_and_password():
    assert parse_credentials(creds(username="io-node", password="Abc123xyz")) == \
        ("io-node", "Abc123xyz")


def test_it_is_read_from_the_path_given(tmp_path):
    path = tmp_path / "mqtt_credentials.json"
    path.write_text(creds(username="io-node", password="Abc123xyz"))

    assert load_credentials(str(path)) == ("io-node", "Abc123xyz")


def test_a_missing_file_says_to_redeploy(tmp_path):
    with pytest.raises(CredentialsError, match="deploy"):
        load_credentials(str(tmp_path / "absent.json"))


@pytest.mark.parametrize("text", [
    "not json at all",
    "[]",
    creds(username="io-node"),
    creds(password="Abc123xyz"),
    creds(username="", password="Abc123xyz"),
    creds(username="io-node", password=""),
    creds(username="io-node", password=12345),
])
def test_anything_incomplete_is_refused(text):
    with pytest.raises(CredentialsError):
        parse_credentials(text)


@pytest.mark.parametrize("password", ['ab"cd', "ab\\cd", "ab,cd", "ab cd", "ab\ncd", "abcé"])
def test_characters_the_at_syntax_cannot_carry_are_refused(password):
    with pytest.raises(CredentialsError):
        parse_credentials(creds(username="io-node", password=password))


def test_the_modems_length_limit_is_enforced_here():
    ok = "a" * MAX_FIELD_LENGTH
    assert parse_credentials(creds(username="io-node", password=ok))[1] == ok

    with pytest.raises(CredentialsError, match="at most"):
        parse_credentials(creds(username="io-node", password=ok + "a"))


def test_the_error_never_quotes_the_password():
    with pytest.raises(CredentialsError) as info:
        parse_credentials(creds(username="io-node", password='Hunter2"'))
    assert "Hunter2" not in str(info.value)


def test_a_credentials_fault_flashes_its_own_code_not_the_brokers():
    code = code_for_error(CredentialsError("no credentials"))
    assert code == CODE_CREDENTIALS
    assert code != CODE_BROKER
