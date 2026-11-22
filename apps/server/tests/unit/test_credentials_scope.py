"""Host patterns, credential scopes and the agent and env grants."""

import uuid
from collections.abc import AsyncGenerator, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

import pytest

from src.credentials.service import (
    AgentCredentials,
    CredentialDeniedError,
    env_name,
    host_allowed,
    host_pattern,
)
from src.db.engine import Sessions
from src.db.models import (
    Credential,
    CredentialEvent,
    CredentialStatus,
    Execution,
    ExecutionKind,
    SecretType,
)
from src.observe.credentials import CredentialSecret, Vault
from src.policy.redact import REDACTED, Redactor

KEY = "vBEIpEgikO_ICioUfkcoQ2wQc9mtSBVhuxtZW05nJqM="
OWNER = uuid.uuid4()
CHAT = uuid.uuid4()


@pytest.mark.parametrize(
    ("value", "pattern"),
    [
        ("Example.COM", "example.com"),
        ("*.Example.com.", "*.example.com"),
    ],
)
def test_host_pattern_normalizes(value: str, pattern: str) -> None:
    assert host_pattern(value) == pattern


@pytest.mark.parametrize("value", ["*", "*.", "example", "http://x", "*.*.com"])
def test_host_pattern_rejects_invalid(value: str) -> None:
    with pytest.raises(ValueError, match="host"):
        host_pattern(value)


def test_host_allowed_matches_exact_and_subdomains() -> None:
    assert host_allowed("api.example.com", ["example.com"]) is False
    assert host_allowed("example.com", ["example.com"]) is True
    assert host_allowed("api.example.com", ["*.example.com"]) is True
    assert host_allowed("example.com", ["*.example.com"]) is False


@pytest.mark.parametrize("value", ["API_TOKEN", "_X", "A1", "A" * 64])
def test_env_name_accepts_uppercase_identifiers(value: str) -> None:
    assert env_name(value) == value


@pytest.mark.parametrize("value", ["api_token", "1A", "A-B", "", "A" * 65, "A B"])
def test_env_name_rejects_malformed_names(value: str) -> None:
    with pytest.raises(ValueError, match="uppercase"):
        env_name(value)


@pytest.mark.parametrize(
    "value", ["PATH", "HOME", "NODE_OPTIONS", "BASH_ENV", "LD_PRELOAD", "PYTHONPATH", "XAVIER_X"]
)
def test_env_name_rejects_names_the_runtime_uses(value: str) -> None:
    with pytest.raises(ValueError, match="reserved"):
        env_name(value)


class FakeSession:
    def __init__(self, rows: list[Credential]) -> None:
        self.rows = {row.id: row for row in rows}
        self.added: list[object] = []

    async def get(self, model: type[Credential], key: UUID) -> Credential | None:
        del model
        return self.rows.get(key)

    async def scalars(self, statement: object) -> FakeSession:
        del statement
        return self

    def all(self) -> list[Credential]:
        return list(self.rows.values())

    def __iter__(self) -> Iterator[Credential]:
        return iter(self.all())

    def add(self, row: object) -> None:
        self.added.append(row)

    def events(self) -> list[CredentialEvent]:
        return [row for row in self.added if isinstance(row, CredentialEvent)]


class FakeSessions:
    """Answers queries with fixed rows, as the scoped query would select them."""

    def __init__(self, *rows: Credential) -> None:
        self.session = FakeSession(list(rows))

    @asynccontextmanager
    async def __call__(self) -> AsyncGenerator[FakeSession]:
        yield self.session

    @asynccontextmanager
    async def begin(self) -> AsyncGenerator[FakeSession]:
        yield self.session


def _credential(value: str = "s3cret", **fields: object) -> Credential:
    values: dict[str, object] = {
        "id": uuid.uuid4(),
        "created_by": OWNER,
        "name": "c",
        "secret_type": SecretType.TOKEN,
        "header": "Authorization",
        "prefix": "Bearer ",
        "username": None,
        "secret": Vault(KEY).seal(CredentialSecret(value=value)),
        "hosts": ["api.example.com"],
        "grant_agent": True,
        "grant_env": None,
        "chat_id": None,
        "status": CredentialStatus.ACTIVE,
        "expires_at": None,
        "version": 1,
    }
    return Credential(**(values | fields))


def _agent(*rows: Credential) -> tuple[AgentCredentials, FakeSession, Redactor]:
    sessions = FakeSessions(*rows)
    redactor = Redactor([])
    return (
        AgentCredentials(cast(Sessions, sessions), Vault(KEY), redactor),
        sessions.session,
        redactor,
    )


async def test_agent_token_goes_into_its_header_on_matching_hosts_and_is_audited() -> None:
    credential = _credential()
    credentials, session, redactor = _agent(credential)

    headers = await credentials.headers(
        credential.id, chat_id=CHAT, owner=OWNER, action_id=None, host="api.example.com"
    )

    assert headers == {"Authorization": "Bearer s3cret"}
    [event] = session.events()
    assert (event.kind, event.chat_id) == ("used", CHAT)
    assert "s3cret" not in event.detail
    assert redactor("leaked s3cret") == f"leaked {REDACTED}"


@pytest.mark.parametrize(
    ("fields", "host"),
    [
        ({}, "evil.example.net"),
        ({"created_by": uuid.uuid4()}, "api.example.com"),
        ({"chat_id": uuid.uuid4()}, "api.example.com"),
        ({"grant_agent": False}, "api.example.com"),
        ({"status": CredentialStatus.REVOKED}, "api.example.com"),
        ({"expires_at": datetime.now(UTC) - timedelta(minutes=1)}, "api.example.com"),
        ({"secret_type": SecretType.LOGIN, "username": "me"}, "api.example.com"),
    ],
    ids=["other_host", "other_owner", "other_chat", "not_granted", "revoked", "expired", "login"],
)
async def test_agent_may_not_send_a_token_out_of_its_scope(
    fields: dict[str, object], host: str
) -> None:
    credential = _credential()
    for name, value in fields.items():
        setattr(credential, name, value)
    credentials, _session, _redactor = _agent(credential)

    with pytest.raises(CredentialDeniedError):
        await credentials.headers(
            credential.id, chat_id=CHAT, owner=OWNER, action_id=None, host=host
        )


async def test_agent_login_is_typed_only_on_its_hosts() -> None:
    credential = _credential("pw", secret_type=SecretType.LOGIN, username="me", hosts=["*.ex.com"])
    credentials, session, _redactor = _agent(credential)

    secret, hosts = await credentials.for_browser(
        credential.id, chat_id=CHAT, owner=OWNER, action_id=uuid.uuid4()
    )

    assert (secret, hosts) == ("pw", ["*.ex.com"])
    assert [event.kind for event in session.events()] == ["used"]


async def test_env_grant_sets_variables_audits_each_and_redacts_values() -> None:
    chat_token = _credential("chat-value", grant_env="API_TOKEN", chat_id=CHAT)
    global_token = _credential("global-value", grant_env="API_TOKEN")
    other = _credential("other-value", grant_env="OTHER")
    credentials, session, redactor = _agent(chat_token, global_token, other)
    execution = Execution(
        id=uuid.uuid4(),
        created_by=OWNER,
        chat_id=CHAT,
        action_id=uuid.uuid4(),
        kind=ExecutionKind.CODE,
    )

    env = await credentials.environment(execution)

    assert env == {"API_TOKEN": "chat-value", "OTHER": "other-value"}
    events = session.events()
    assert sorted(event.detail for event in events) == ["env API_TOKEN v1", "env OTHER v1"]
    assert all(event.action_id == execution.action_id for event in events)
    assert redactor("chat-value other-value") == f"{REDACTED} {REDACTED}"


async def test_agent_context_lists_names_and_variables_but_no_values() -> None:
    credential = _credential("s3cret", name="GitHub", grant_env="GH_TOKEN")
    credentials, _session, _redactor = _agent(credential)

    [listing] = await credentials.available(CHAT, OWNER)

    assert listing["name"] == "GitHub"
    assert listing["env"] == "GH_TOKEN"
    assert "s3cret" not in repr(listing)
