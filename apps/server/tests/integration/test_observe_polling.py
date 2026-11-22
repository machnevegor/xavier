"""Observe polling against PostgreSQL, S3 and Temporal; sites are served by a mock transport.

The browser test renders https://quotes.toscrape.com/js/, so it needs the desktop image,
the sandbox firewall and internet access:

docker build -t xavier-desktop:local apps/server/images/desktop
docker compose run --rm firewall-init
"""

import asyncio
import hashlib
import hmac
import json
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from uuid import UUID

import aiodocker
import httpx2
import pytest
from sqlalchemy import select
from temporalio.client import Client
from temporalio.worker import Worker
from types_aiobotocore_s3.client import S3Client

from src.compute.docker_backend import DockerComputerBackend
from src.config import Settings
from src.db.engine import Sessions, create_engine, create_sessions, migrate
from src.db.models import (
    Alert,
    AlertKind,
    AlertStatus,
    Credential,
    SecretType,
    Source,
    SourcePauseReason,
    SourceStatus,
    Subscription,
    SubscriptionChannel,
)
from src.docker.client import DockerHost
from src.observe.credentials import Credentials, CredentialSecret, Vault
from src.observe.fetch import BrowserRenderer, HttpFetcher
from src.observe.service import Observe, Sources
from src.observe.spec import Schedule, SourceSpec
from src.orchestration.activities import ObserveActivities
from src.orchestration.client import connect_temporal
from src.orchestration.schedules import SourceSchedules
from src.orchestration.workflows import ObservePollWorkflow
from src.storage.blobs import S3Adapter
from tests.support import add_user

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

PUBLIC_ADDRESS = "93.184.215.14"
EVERY_DAY = {"interval": "P1D"}


@dataclass
class Site:
    """Responses by ``host/path?query``; a missing page is a 404."""

    pages: dict[str, tuple[int, object]] = field(default_factory=dict)
    tokens: set[str] = field(default_factory=set)
    """Accepted bearer tokens of protected pages."""
    protected: set[str] = field(default_factory=set)
    hooks: list[httpx2.Request] = field(default_factory=list)

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        page = f"{request.headers['host']}{request.url.raw_path.decode()}"
        if request.method == "POST" and page.startswith("hooks.example.com/"):
            self.hooks.append(request)
            return httpx2.Response(204)
        if page in self.protected:
            token = request.headers.get("authorization", "").removeprefix("Bearer ")
            if token not in self.tokens:
                return httpx2.Response(401)
        if page not in self.pages:
            return httpx2.Response(404)
        status, body = self.pages[page]
        return httpx2.Response(status, json=body)


@dataclass
class Stack:
    site: Site
    sessions: Sessions
    owner: UUID
    sources: Sources
    vault: Vault
    schedules: SourceSchedules
    poll: Callable[[UUID], Awaitable[None]]


async def _public(_host: str, _port: int) -> list[str]:
    return [PUBLIC_ADDRESS]


@pytest.fixture
async def sessions(settings: Settings) -> AsyncIterator[Sessions]:
    await asyncio.to_thread(migrate, settings.database)
    engine = create_engine(settings.database)
    try:
        yield create_sessions(engine)
    finally:
        await engine.dispose()


@pytest.fixture
async def client(s3: S3Client, settings: Settings) -> Client:
    return await connect_temporal(settings, s3)


@pytest.fixture
async def docker() -> AsyncIterator[aiodocker.Docker]:
    client = aiodocker.Docker()
    try:
        yield client
    finally:
        await client.close()


@pytest.fixture
async def stack(
    settings: Settings,
    sessions: Sessions,
    s3: S3Client,
    client: Client,
    docker: aiodocker.Docker,
) -> AsyncIterator[Stack]:
    site = Site()
    fetcher = HttpFetcher(_public, httpx2.MockTransport(site.handle))
    blobs = S3Adapter(s3, settings.s3.artifacts_bucket)
    vault = Vault(settings.observe.credentials_key.get_secret_value())
    host = DockerHost(docker, settings.worker.id)
    observe = Observe(
        settings=settings.observe,
        sessions=sessions,
        blobs=blobs,
        fetcher=fetcher,
        renderer=BrowserRenderer(DockerComputerBackend(host, settings.computer), host),
        credentials=Credentials(sessions, vault, fetcher),
        vault=vault,
    )
    task_queue = f"test-observe-{uuid.uuid4()}"
    schedules = SourceSchedules(client, task_queue)
    activities = ObserveActivities(sessions, observe, schedules, task_queue)
    created: list[UUID] = []

    async def poll(source_id: UUID) -> None:
        created.append(source_id)
        await client.execute_workflow(
            ObservePollWorkflow.run,
            source_id,
            id=f"test-poll-{uuid.uuid4()}",
            task_queue=task_queue,
        )

    worker = Worker(
        client, task_queue=task_queue, workflows=[ObservePollWorkflow], activities=activities.all()
    )
    async with worker:
        try:
            yield Stack(
                site,
                sessions,
                await add_user(sessions),
                Sources(sessions, blobs),
                vault,
                schedules,
                poll,
            )
        finally:
            for source_id in created:
                await schedules.delete(source_id)
            await fetcher.close()


async def _published(stack: Stack, spec: dict[str, object]) -> UUID:
    source = await stack.sources.draft(
        f"test-{uuid.uuid4().hex[:8]}", SourceSpec.model_validate(spec), None, stack.owner
    )
    await stack.sources.publish(source.id, stack.owner)
    return source.id


async def _alerts(stack: Stack, source_id: UUID, kind: AlertKind) -> list[Alert]:
    async with stack.sessions() as session:
        rows = await session.scalars(
            select(Alert).where(Alert.source_id == source_id, Alert.kind == kind).order_by(Alert.id)
        )
        return list(rows.all())


async def _source(stack: Stack, source_id: UUID) -> Source:
    async with stack.sessions() as session:
        return await session.get_one(Source, source_id)


async def test_a_version_is_stored_only_when_the_normalized_value_changes(stack: Stack) -> None:
    """AT-01."""
    host = f"{uuid.uuid4().hex[:8]}.example.com"
    stack.site.pages[f"{host}/price"] = (200, {"price": 10, "updated_at": "t1"})
    source_id = await _published(
        stack,
        {
            "url": f"https://{host}/price",
            "normalize": {"ignore": ["$.updated_at"]},
            "schedule": EVERY_DAY,
        },
    )

    await stack.poll(source_id)
    stack.site.pages[f"{host}/price"] = (200, {"price": 10, "updated_at": "t2"})
    await stack.poll(source_id)

    [first] = await stack.sources.versions(source_id, stack.owner, "", None, 10)
    assert await stack.sources.value(first) == {"price": 10}
    assert await _alerts(stack, source_id, AlertKind.CHANGED) == []

    stack.site.pages[f"{host}/price"] = (200, {"price": 12, "currency": "EUR"})
    await stack.poll(source_id)

    second, _first = await stack.sources.versions(source_id, stack.owner, "", None, 10)
    assert second.change_count == 2
    [changed] = await _alerts(stack, source_id, AlertKind.CHANGED)
    assert changed.version_id == second.id
    [schema] = await _alerts(stack, source_id, AlertKind.SCHEMA_CHANGED)
    assert schema.data == {
        "url": f"https://{host}/price",
        "schema_changes": [{"path": "$.currency", "change": "added", "to": "string"}],
    }
    diff = await stack.sources.diff(source_id, stack.owner, first.id, second.id)
    assert diff["changes"] == [
        {"op": "changed", "path": "$.price", "old": 10, "new": 12},
        {"op": "added", "path": "$.currency", "new": "EUR"},
    ]
    source = await _source(stack, source_id)
    assert source.last_success_at is not None
    assert source.last_error is None


async def test_children_are_followed_once_and_missing_ones_become_gone(stack: Stack) -> None:
    """AT-02: dependent resources, cycles and disappearance."""
    host = f"{uuid.uuid4().hex[:8]}.example.com"
    pages = stack.site.pages
    pages[f"{host}/list?page=1"] = (200, {"ids": [1, 2], "next": 2})
    pages[f"{host}/list?page=2"] = (200, {"ids": [3], "next": 1})
    for item in (1, 2):
        pages[f"{host}/items/{item}"] = (200, {"id": item})
    source_id = await _published(
        stack,
        {
            "url": f"https://{host}/list?page=1",
            "children": [
                {
                    "name": "page",
                    "select": "$.next",
                    "url": f"https://{host}/list?page={{value}}",
                    "recursive": True,
                },
                {"name": "item", "select": "$.ids[*]", "url": f"https://{host}/items/{{value}}"},
            ],
            "schedule": EVERY_DAY,
        },
    )

    await stack.poll(source_id)

    keys = {
        version.resource_key
        for version in await stack.sources.versions(source_id, stack.owner, None, None, 50)
    }
    assert keys == {"", "page:2", "page:1", "item:1", "item:2"}

    pages[f"{host}/list?page=1"] = (200, {"ids": [1], "next": 2})
    await stack.poll(source_id)

    [gone] = await _alerts(stack, source_id, AlertKind.GONE)
    assert gone.resource_key == "item:2"
    [latest] = await stack.sources.versions(source_id, stack.owner, "item:2", None, 1)
    assert latest.gone

    pages[f"{host}/list?page=1"] = (200, {"ids": [1, 2], "next": 2})
    await stack.poll(source_id)

    [resolved] = await _alerts(stack, source_id, AlertKind.GONE)
    assert resolved.status is AlertStatus.RESOLVED


async def test_limits_make_a_poll_incomplete_so_nothing_is_reported_gone(stack: Stack) -> None:
    host = f"{uuid.uuid4().hex[:8]}.example.com"
    pages = stack.site.pages
    pages[f"{host}/list"] = (200, {"ids": [1, 2], "total": 2})
    for item in (1, 2):
        pages[f"{host}/items/{item}"] = (200, {"id": item})
    spec: dict[str, object] = {
        "url": f"https://{host}/list",
        "children": [
            {
                "name": "item",
                "select": "$.ids[*]",
                "total": "$.total",
                "url": f"https://{host}/items/{{value}}",
            }
        ],
        "schedule": EVERY_DAY,
    }
    source_id = await _published(stack, spec)
    await stack.poll(source_id)

    pages[f"{host}/list"] = (200, {"ids": [1], "total": 2})
    await stack.poll(source_id)
    pages[f"{host}/list"] = (200, {"ids": [1, 2], "total": 2})
    await stack.sources.revise(
        source_id,
        "limited",
        SourceSpec.model_validate({**spec, "limits": {"max_fanout": 1}}),
        stack.owner,
    )
    await stack.sources.publish(source_id, stack.owner)
    await stack.poll(source_id)
    await stack.poll(source_id)

    assert await _alerts(stack, source_id, AlertKind.GONE) == []
    limits = await _alerts(stack, source_id, AlertKind.LIMIT)
    assert [alert.data for alert in limits] == [
        {"limit": "incomplete:item"},
        {"limit": "max_fanout:item"},
    ]
    assert limits[1].occurrences == 2


async def test_a_draft_is_not_polled_until_published(stack: Stack) -> None:
    """AT-04."""
    host = f"{uuid.uuid4().hex[:8]}.example.com"
    stack.site.pages[f"{host}/"] = (200, {"a": 1})
    draft = await stack.sources.draft(
        "draft",
        SourceSpec.model_validate({"url": f"https://{host}/", "schedule": EVERY_DAY}),
        None,
        stack.owner,
    )

    await stack.poll(draft.id)

    assert await stack.sources.versions(draft.id, stack.owner, None, None, 10) == []
    await stack.sources.publish(draft.id, stack.owner)
    await stack.poll(draft.id)
    assert len(await stack.sources.versions(draft.id, stack.owner, None, None, 10)) == 1


async def test_a_rejected_credential_pauses_only_the_sources_using_it(stack: Stack) -> None:
    """AT-16."""
    host = f"{uuid.uuid4().hex[:8]}.example.com"
    stack.site.pages[f"{host}/a"] = (200, {"a": 1})
    stack.site.pages[f"{host}/b"] = (200, {"b": 1})
    stack.site.protected |= {f"{host}/a", f"{host}/b"}
    stack.site.tokens |= {"good"}
    expired, valid = uuid.uuid7(), uuid.uuid7()
    async with stack.sessions.begin() as session:
        for credential_id, token in ((expired, "expired"), (valid, "good")):
            session.add(
                Credential(
                    id=credential_id,
                    created_by=stack.owner,
                    name=f"bearer-{token}",
                    secret_type=SecretType.TOKEN,
                    header="Authorization",
                    prefix="Bearer ",
                    secret=stack.vault.seal(CredentialSecret(value=token)),
                    hosts=[host],
                    grant_watch=True,
                )
            )
    first = await _published(
        stack, {"url": f"https://{host}/a", "credential_id": str(expired), "schedule": EVERY_DAY}
    )
    second = await _published(
        stack, {"url": f"https://{host}/b", "credential_id": str(expired), "schedule": EVERY_DAY}
    )
    other = await _published(
        stack, {"url": f"https://{host}/b", "credential_id": str(valid), "schedule": EVERY_DAY}
    )
    for source_id in (first, second, other):
        await stack.schedules.put(source_id, Schedule.model_validate(EVERY_DAY), paused=True)

    await stack.poll(first)
    await stack.poll(other)

    for source_id in (first, second):
        source = await _source(stack, source_id)
        assert (source.status, source.pause_reason) == (
            SourceStatus.PAUSED,
            SourcePauseReason.AUTH_LOST,
        )
        assert len(await _alerts(stack, source_id, AlertKind.AUTH_LOST)) == 1
    assert (await _source(stack, other)).status is SourceStatus.PUBLISHED
    assert len(await stack.sources.versions(other, stack.owner, None, None, 10)) == 1
    async with stack.sessions() as session:
        assert (await session.get_one(Credential, expired)).failed_at is not None

    async with stack.sessions.begin() as session:
        credential = await session.get_one(Credential, expired)
        credential.secret = stack.vault.seal(CredentialSecret(value="good"))
        credential.failed_at = None
    assert sorted(await stack.sources.credential_replaced(expired)) == sorted([first, second])
    await stack.poll(first)

    assert (await _source(stack, first)).status is SourceStatus.PUBLISHED
    [lost] = await _alerts(stack, first, AlertKind.AUTH_LOST)
    assert lost.status is AlertStatus.RESOLVED
    assert len(await stack.sources.versions(first, stack.owner, None, None, 10)) == 1


async def test_new_alerts_are_posted_to_webhooks_with_a_signature(stack: Stack) -> None:
    host = f"{uuid.uuid4().hex[:8]}.example.com"
    stack.site.pages[f"{host}/"] = (200, {"a": 1})
    source_id = await _published(stack, {"url": f"https://{host}/", "schedule": EVERY_DAY})
    secret = "webhook-secret-0123456789"
    async with stack.sessions.begin() as session:
        session.add(
            Subscription(
                id=uuid.uuid7(),
                created_by=stack.owner,
                channel=SubscriptionChannel.WEBHOOK,
                source_id=source_id,
                kinds=[AlertKind.CHANGED.value],
                url="https://hooks.example.com/xavier",
                secret=stack.vault.encrypt(secret),
            )
        )
    await stack.poll(source_id)
    stack.site.pages[f"{host}/"] = (200, {"a": 2})

    await stack.poll(source_id)

    # Global webhooks other tests leave in the shared database receive the alert too.
    [hook] = [request for request in stack.site.hooks if request.url.path == "/xavier"]
    timestamp, signature = (
        part.split("=", 1)[1] for part in hook.headers["x-xavier-signature"].split(",")
    )
    expected = hmac.new(secret.encode(), f"{timestamp}.".encode() + hook.content, hashlib.sha256)
    assert hmac.compare_digest(signature, expected.hexdigest())
    payload = json.loads(hook.content)
    assert (payload["source_id"], payload["kind"]) == (str(source_id), "changed")


async def test_browser_mode_observes_pages_rendered_by_javascript(stack: Stack) -> None:
    """AT-03."""
    source_id = await _published(
        stack,
        {
            "url": "https://quotes.toscrape.com/js/",
            "mode": "browser",
            "extract": {"css": ".quote .text"},
            "schedule": EVERY_DAY,
        },
    )
    await stack.poll(source_id)

    version, value = await stack.sources.latest(source_id, stack.owner)
    assert version.content_type == "text/html"
    assert isinstance(value, list)
    assert len(value) == 10
