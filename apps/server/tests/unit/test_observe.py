import hashlib
import uuid
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import cast
from uuid import UUID

import httpx2
import pytest
from pydantic import JsonValue, ValidationError

from src.db.engine import Sessions
from src.db.models import Credential, CredentialEvent, SecretType, Source, SourceStatus
from src.errors import NotFoundError
from src.observe.credentials import Credentials, CredentialSecret, Vault, request_headers
from src.observe.extract import ExtractError, ExtractRule, extract
from src.observe.fetch import FetchResult, HttpFetcher
from src.observe.normalize import Normalization, canonical, fingerprint, normalize
from src.observe.service import Sources
from src.observe.spec import ChildRule, Schedule, SourceSpec, hosts, rules, widening
from src.observe.structure import infer, label, merge, structural_changes, value_changes
from src.policy.model import Mode, Risk
from src.tools.base import ToolContext
from src.tools.observe import WatchDraft, WatchDraftArgs
from tests.fakes import services as tool_services

KEY = "vBEIpEgikO_ICioUfkcoQ2wQc9mtSBVhuxtZW05nJqM="
EVERY_HOUR = {"interval": "PT1H"}
OWNER = uuid.uuid4()


def _spec(**fields: object) -> SourceSpec:
    return SourceSpec.model_validate(
        {"url": "https://api.example.com/items", "schedule": EVERY_HOUR, **fields}
    )


def _result(body: bytes, content_type: str) -> FetchResult:
    return FetchResult(
        url="https://example.com/",
        status=200,
        content_type=content_type,
        headers={},
        body=body,
        truncated=False,
    )


def test_normalization_removes_noise_so_the_fingerprint_is_stable() -> None:
    rules = Normalization.model_validate(
        {"ignore": ["$..updated_at"], "sort": [{"path": "$.items", "key": "id"}]}
    )
    first: JsonValue = {
        "items": [{"id": 2, "updated_at": "t1"}, {"id": 1, "updated_at": "t1"}],
        "updated_at": "t1",
    }
    second: JsonValue = {
        "updated_at": "t2",
        "items": [{"id": 1, "updated_at": "t2"}, {"id": 2, "updated_at": "t2"}],
    }

    normalized = normalize(first, rules)

    assert normalized == {"items": [{"id": 1}, {"id": 2}]}
    assert fingerprint(normalized) == fingerprint(normalize(second, rules))
    assert canonical(normalized) == b'{"items":[{"id":1},{"id":2}]}'


def test_items_without_the_sort_key_go_last() -> None:
    rules = Normalization.model_validate({"sort": [{"path": "$", "key": "id"}]})

    assert normalize([{"x": 1}, {"id": "b"}, {"id": "a"}], rules) == [
        {"id": "a"},
        {"id": "b"},
        {"x": 1},
    ]


def test_invalid_jsonpath_is_rejected() -> None:
    with pytest.raises(ValidationError, match="invalid JSONPath"):
        Normalization.model_validate({"ignore": ["$[?("]})


def test_structure_merges_list_item_variants() -> None:
    schema = infer({"items": [{"a": 1}, {"a": "x", "b": None}], "empty": []})

    assert schema == {
        "type": "object",
        "fields": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "fields": {
                        "a": {"anyOf": [{"type": "integer"}, {"type": "string"}]},
                        "b": {"type": "null"},
                    },
                },
            },
            "empty": {"type": "array", "items": None},
        },
    }
    assert label(merge({"type": "string"}, {"type": "null"})) == "null|string"


def test_structural_changes_report_added_removed_and_retyped_fields() -> None:
    old = infer({"a": 1, "b": "x", "list": [], "nested": {"c": True}})
    new = infer({"a": "1", "list": [{"d": 1}], "nested": {"c": True, "e": 2}, "f g": 0})

    assert structural_changes(old, new) == [
        {"path": "$.a", "change": "type_changed", "from": "integer", "to": "string"},
        {"path": "$.b", "change": "removed", "from": "string"},
        {"path": '$["f g"]', "change": "added", "to": "integer"},
        {"path": "$.nested.e", "change": "added", "to": "integer"},
    ]


def test_value_changes_are_listed_by_path_and_counted_past_the_limit() -> None:
    old: JsonValue = {"a": 1, "b": [1, 2, 3], "c": {"d": "x" * 300}}
    new: JsonValue = {"a": 2, "b": [1, 5], "c": {}, "e": True}

    changes, total = value_changes(old, new)

    assert changes == [
        {"op": "changed", "path": "$.a", "old": 1, "new": 2},
        {"op": "changed", "path": "$.b[1]", "old": 2, "new": 5},
        {"op": "removed", "path": "$.b[2]", "old": 3},
        {"op": "removed", "path": "$.c.d", "old": "x" * 200 + "…"},
        {"op": "added", "path": "$.e", "new": True},
    ]
    assert total == 5
    assert value_changes(old, new, limit=2) == (changes[:2], 5)
    assert value_changes({"a": 1}, {"a": 1.0}) == (
        [{"op": "changed", "path": "$.a", "old": 1, "new": 1.0}],
        1,
    )


def test_extract_without_a_rule_depends_on_the_content_type() -> None:
    html = b"<html><head><title>T</title></head><body><p>Hello</p></body></html>"
    binary = b"\x00\x01"

    assert extract(_result(b'{"a": [1]}', "application/json"), None) == {"a": [1]}
    assert extract(_result(html, "text/html"), None) == {"title": "T", "text": "Hello"}
    assert extract(_result(b"plain", "text/plain"), None) == "plain"
    assert extract(_result(binary, "image/png"), None) == {
        "size": 2,
        "sha256": hashlib.sha256(binary).hexdigest(),
    }


def test_extract_rules_select_json_and_html_values() -> None:
    page = b'<ul><li><a href="/1">One</a></li><li><a href="/2">Two <b>2</b></a></li></ul>'

    assert extract(
        _result(b'{"items": [{"id": 1}, {"id": 2}]}', "application/json"),
        ExtractRule(jsonpath="$.items[*].id"),
    ) == [1, 2]
    assert extract(_result(page, "text/html"), ExtractRule(css="li a")) == ["One", "Two 2"]
    assert extract(_result(page, "text/html"), ExtractRule(css="a", attribute="href")) == [
        "/1",
        "/2",
    ]
    with pytest.raises(ExtractError):
        extract(_result(b"{}", "application/json"), ExtractRule(css="a"))
    with pytest.raises(ExtractError):
        extract(_result(b"not json", "application/json"), ExtractRule(jsonpath="$.a"))


def test_extract_rule_needs_exactly_one_selector() -> None:
    with pytest.raises(ValidationError):
        ExtractRule()
    with pytest.raises(ValidationError):
        ExtractRule(jsonpath="$.a", css="a")
    with pytest.raises(ValidationError):
        ExtractRule(jsonpath="$.a", attribute="href")


def test_source_spec_keeps_secrets_out_of_headers() -> None:
    with pytest.raises(ValidationError, match="credential"):
        _spec(headers={"Authorization": "Bearer x"})
    with pytest.raises(ValidationError, match="POST"):
        _spec(body="{}")
    with pytest.raises(ValidationError, match="browser"):
        _spec(mode="browser", method="POST")
    with pytest.raises(ValidationError):
        _spec(url="ftp://example.com/")


def test_schedule_needs_one_valid_timing() -> None:
    assert Schedule.model_validate({"cron": "0 9 * * 1-5"}).cron == "0 9 * * 1-5"
    assert Schedule.model_validate(EVERY_HOUR).interval == timedelta(hours=1)
    for invalid in (
        {},
        {"interval": "PT1H", "cron": "* * * * *"},
        {"interval": "PT30S"},
        {"cron": "* * *"},
        {"cron": "* * * * *", "time_zone": "Mars/Base"},
    ):
        with pytest.raises(ValidationError):
            Schedule.model_validate(invalid)


def test_child_rules_build_urls_and_keys_from_values() -> None:
    rule = ChildRule(name="item", select="$[*].id", url="https://cdn.example.com/items/{value}")

    assert rule.child_url("a b/c") == "https://cdn.example.com/items/a%20b%2Fc"
    assert rule.key(7) == "item:7"
    with pytest.raises(ValidationError, match="placeholder"):
        ChildRule(name="item", select="$", url="https://{value}.example.com/")
    with pytest.raises(ValidationError, match="placeholder"):
        ChildRule(name="item", select="$", url="https://example.com/")


def test_hosts_cover_nested_child_rules() -> None:
    spec = _spec(
        children=[
            {
                "name": "page",
                "select": "$.next",
                "url": "https://api.example.com/items?page={value}",
                "recursive": True,
                "children": [
                    {"name": "item", "select": "$[*]", "url": "https://cdn.example.com/{value}"}
                ],
            }
        ]
    )

    assert [rule.name for rule in rules(spec)] == ["page", "item"]
    assert hosts(spec) == {"api.example.com", "cdn.example.com"}


def test_widening_lists_new_hosts_credentials_and_raised_limits() -> None:
    credential = uuid.uuid4()
    current = _spec(limits={"max_depth": 1})
    same_host = _spec(url="https://api.example.com/other", limits={"max_depth": 1})
    wider = _spec(
        credential_id=str(credential),
        limits={"max_depth": 2},
        children=[{"name": "item", "select": "$[*]", "url": "https://cdn.example.com/{value}"}],
    )

    assert widening(None, current) == ["host api.example.com"]
    assert widening(current, same_host) == []
    assert widening(current, wider) == [
        "host cdn.example.com",
        f"credential {credential}",
        "limit max_depth",
    ]
    assert widening(current, _spec()) == ["limit max_depth"]


def test_vault_round_trips_secrets_only_with_its_key() -> None:
    vault = Vault(KEY)
    secret = CredentialSecret(value="token", refresh_token="refresh")

    sealed = vault.seal(secret)

    assert "token" not in sealed
    assert vault.open(sealed) == secret
    with pytest.raises(ValueError, match="credentials_key"):
        Vault("Ey8t4bE3fV1u9gJ0u2Mbx3nV-0dQhGQfN1cH2kEJbS0=").open(sealed)


class FakeSources(Sources):
    def __init__(self, *rows: Source) -> None:
        self.rows = {row.id: row for row in rows}

    async def source(self, source_id: UUID, owner: UUID) -> Source:
        if source_id not in self.rows or self.rows[source_id].created_by != owner:
            raise NotFoundError(f"source {source_id} not found")
        return self.rows[source_id]


def _tool_context(*sources: Source) -> ToolContext:
    return ToolContext(
        chat_id=uuid.uuid4(),
        owner=OWNER,
        action_id=uuid.uuid4(),
        mode=Mode.AUTONOMOUS,
        execute_network=False,
        image_input=False,
        services=tool_services(sources=FakeSources(*sources)),
    )


async def test_watch_draft_is_sensitive_only_when_it_widens_a_polled_source() -> None:
    polled = _spec()
    published = Source(
        id=uuid.uuid4(),
        created_by=OWNER,
        name="s",
        status=SourceStatus.PUBLISHED,
        spec=polled.model_dump(mode="json"),
    )
    draft = Source(
        id=uuid.uuid4(),
        created_by=OWNER,
        name="d",
        status=SourceStatus.DRAFT,
        spec=polled.model_dump(mode="json"),
    )
    ctx = _tool_context(published, draft)
    other_host = _spec(url="https://other.example.com/")

    def args(spec: SourceSpec, source_id: UUID | None) -> WatchDraftArgs:
        return WatchDraftArgs(name="s", spec=spec, source_id=source_id)

    assert await WatchDraft().classify(args(other_host, None), ctx) is Risk.WRITE
    assert await WatchDraft().classify(args(other_host, draft.id), ctx) is Risk.WRITE
    assert await WatchDraft().classify(args(polled, published.id), ctx) is Risk.WRITE
    assert await WatchDraft().classify(args(other_host, published.id), ctx) is Risk.SENSITIVE


async def test_watch_draft_does_not_see_sources_of_another_user() -> None:
    foreign = Source(
        id=uuid.uuid4(),
        created_by=uuid.uuid4(),
        name="s",
        status=SourceStatus.PUBLISHED,
        spec=_spec().model_dump(mode="json"),
    )
    ctx = _tool_context(foreign)
    args = WatchDraftArgs(name="s", spec=_spec(url="https://other.example.com/"))

    assert (
        await WatchDraft().classify(args.model_copy(update={"source_id": foreign.id}), ctx)
        is Risk.WRITE
    )


@pytest.mark.parametrize(
    ("header", "prefix", "expected"),
    [
        (None, "", {"Authorization": "s"}),
        (None, "Bearer ", {"Authorization": "Bearer s"}),
        ("X-API-Key", "", {"X-API-Key": "s"}),
        ("Cookie", "session=", {"Cookie": "session=s"}),
    ],
)
def test_tokens_are_sent_as_header_with_prefix_and_value(
    header: str | None, prefix: str, expected: dict[str, str]
) -> None:
    credential = _credential(header=header, prefix=prefix)

    assert request_headers(credential, CredentialSecret(value="s")) == expected


def test_logins_are_never_sent_in_headers() -> None:
    credential = _credential(secret_type=SecretType.LOGIN, username="me")

    with pytest.raises(ValueError, match="login"):
        request_headers(credential, CredentialSecret(value="password"))


class FakeSession:
    def __init__(self, credential: Credential | None) -> None:
        self.credential = credential
        self.added: list[object] = []
        self.executed = 0

    async def scalar(self, statement: object) -> Credential | None:
        del statement
        return self.credential

    async def execute(self, statement: object) -> None:
        del statement
        self.executed += 1

    def add(self, row: object) -> None:
        self.added.append(row)


class FakeSessions:
    """Answers every credential lookup with one row, as the scoped query would."""

    def __init__(self, credential: Credential | None) -> None:
        self.session = FakeSession(credential)

    @asynccontextmanager
    async def __call__(self) -> AsyncGenerator[FakeSession]:
        yield self.session

    @asynccontextmanager
    async def begin(self) -> AsyncGenerator[FakeSession]:
        yield self.session


async def _resolve(host: str, _port: int) -> list[str]:
    del host
    return ["93.184.216.34"]


def _credential(**fields: object) -> Credential:
    values: dict[str, object] = {
        "id": uuid.uuid4(),
        "created_by": OWNER,
        "name": "c",
        "secret_type": SecretType.TOKEN,
        "header": None,
        "prefix": "",
        "refresh_url": None,
        "secret": "",
        "hosts": ["api.example.com"],
        "grant_watch": True,
        "chat_id": None,
    }
    return Credential(**(values | fields))


def _watch(
    credential: Credential | None, handle: Callable[[httpx2.Request], httpx2.Response]
) -> tuple[Credentials, FakeSessions]:
    sessions = FakeSessions(credential)
    fetcher = HttpFetcher(_resolve, transport=httpx2.MockTransport(handle))
    return Credentials(cast(Sessions, sessions), Vault(KEY), fetcher), sessions


def _unreachable(request: httpx2.Request) -> httpx2.Response:
    raise AssertionError(f"unexpected request to {request.url}")


async def test_watch_sends_the_token_only_to_urls_on_the_credential_hosts() -> None:
    credential = _credential(hosts=["api.example.com"], prefix="Bearer ")
    credential.secret = Vault(KEY).seal(CredentialSecret(value="tok"))
    credentials, _sessions = _watch(credential, _unreachable)

    root = await credentials.headers(
        credential.id, owner=OWNER, chat_id=None, url="https://api.example.com/items"
    )
    child = await credentials.headers(
        credential.id, owner=OWNER, chat_id=None, url="https://cdn.example.com/items/1"
    )

    assert root == {"Authorization": "Bearer tok"}
    assert child is None


async def test_watch_gets_no_headers_for_a_credential_out_of_scope() -> None:
    credentials, _sessions = _watch(None, _unreachable)

    assert (
        await credentials.headers(
            uuid.uuid4(), owner=OWNER, chat_id=None, url="https://api.example.com/items"
        )
        is None
    )


async def test_refresh_posts_only_the_refresh_grant_without_redirects() -> None:
    seen: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, json={"access_token": "new", "refresh_token": "r2"})

    credential = _credential(hosts=["*.example.com"], refresh_url="https://auth.example.com/token")
    credential.secret = Vault(KEY).seal(CredentialSecret(value="old", refresh_token="r1"))
    credentials, sessions = _watch(credential, handle)

    assert await credentials.refresh(credential.id, owner=OWNER, chat_id=None)

    [request] = seen
    assert (request.method, request.headers["host"]) == ("POST", "auth.example.com")
    assert request.content == b"grant_type=refresh_token&refresh_token=r1"
    assert "authorization" not in request.headers
    [event] = sessions.session.added
    assert isinstance(event, CredentialEvent)
    assert event.kind == "refreshed"
    assert "r1" not in event.detail
    assert "new" not in event.detail


@pytest.mark.parametrize(
    ("refresh_url", "refresh_token"),
    [
        ("http://auth.example.com/token", "r1"),
        ("https://auth.example.net/token", "r1"),
        ("https://auth.example.com/token", None),
        (None, "r1"),
    ],
    ids=["plain_http", "foreign_host", "no_refresh_token", "no_endpoint"],
)
async def test_refresh_refuses_tokens_it_may_not_renew(
    refresh_url: str | None, refresh_token: str | None
) -> None:
    credential = _credential(hosts=["*.example.com"], refresh_url=refresh_url)
    credential.secret = Vault(KEY).seal(CredentialSecret(value="old", refresh_token=refresh_token))
    credentials, sessions = _watch(credential, _unreachable)

    assert not await credentials.refresh(credential.id, owner=OWNER, chat_id=None)
    assert sessions.session.added == []


@pytest.mark.parametrize(
    "response",
    [
        httpx2.Response(302, headers={"location": "https://auth.example.com/other"}),
        httpx2.Response(200, content=b"x" * (128 * 1024)),
    ],
    ids=["redirect", "oversized"],
)
async def test_refresh_fails_on_redirects_and_oversized_answers(response: httpx2.Response) -> None:
    seen: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return response

    credential = _credential(
        hosts=["auth.example.com"], refresh_url="https://auth.example.com/token"
    )
    credential.secret = Vault(KEY).seal(CredentialSecret(value="old", refresh_token="r1"))
    credentials, sessions = _watch(credential, handle)

    assert not await credentials.refresh(credential.id, owner=OWNER, chat_id=None)
    assert len(seen) == 1
    assert sessions.session.executed == 0
