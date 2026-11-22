"""REST API v1 against the test stack: auth, isolation, envelopes, filters and errors."""

import asyncio
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

import httpx2
import pytest
from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from temporalio import activity
from temporalio.client import Client
from temporalio.worker import Worker
from types_aiobotocore_s3.client import S3Client

from src.agent.gateway import Gateway
from src.agent.runner import Runner
from src.api.app import create_app
from src.api.dependencies import SESSION_COOKIE
from src.auth.service import Accounts
from src.config import Settings
from src.db.engine import Sessions, migrate
from src.db.models import ExecutionStatus, TurnStatus
from src.orchestration.activities import ChatActivities
from src.orchestration.client import connect_temporal
from src.orchestration.models import (
    ExecutionDone,
    SettleExecution,
    TurnInput,
    TurnOutcome,
    WorkspaceView,
)
from src.orchestration.workflows import ChatWorkflow, ExecutionWorkflow
from src.storage.blobs import BlobStore

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

_PASSWORD = "correct-horse-1"


@activity.defn(name="run_turn")
async def _run_turn(turn: TurnInput) -> TurnOutcome:
    """Finish the turn without a model; these tests check the API, not the agent."""
    del turn
    return TurnOutcome(status=TurnStatus.COMPLETED)


@activity.defn(name="workspace_state")
async def _workspace_state(chat_id: UUID) -> WorkspaceView:
    del chat_id
    return WorkspaceView()


@activity.defn(name="cancel_executions")
async def _cancel_executions(chat_id: UUID) -> None:
    del chat_id


def _chat_activities(
    settings: Settings, sessions: Sessions
) -> list[Callable[..., Awaitable[object]]]:
    """Activities that let a chat be deleted without running an agent turn."""
    # Turns are the stub above; the runner, gateway and blob store are never called.
    chat = ChatActivities(
        settings,
        sessions,
        cast(Runner, None),
        cast(Gateway, None),
        cast(BlobStore, None),
    )
    return [
        chat.ensure_chat,
        chat.load_chat,
        chat.project_chat,
        chat.cancel_actions,
        _run_turn,
        _workspace_state,
        _cancel_executions,
    ]


@pytest.fixture(scope="session")
def spans() -> InMemorySpanExporter:
    """Finished spans of this process; the tracer provider can be set only once."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    return exporter


@pytest.fixture
def queue() -> str:
    return f"test-api-{uuid.uuid4()}"


class Api(httpx2.AsyncClient):
    """The test client plus the credentials of its seeded users."""

    other_cookie: str
    api_key: str
    application: FastAPI


@pytest.fixture
async def api(settings: Settings, s3: S3Client, queue: str) -> AsyncIterator[Api]:
    del s3  # creates the buckets
    await asyncio.to_thread(migrate, settings.database)
    temporal = settings.temporal.model_copy(update={"task_queue": queue})
    app = create_app(settings.model_copy(update={"temporal": temporal}))
    async with (
        app.router.lifespan_context(app),
        Api(transport=httpx2.ASGITransport(app=app), base_url="http://xavier.test") as client,
    ):
        services = app.state.services
        async with services.sessions.begin() as session:
            accounts = Accounts(settings.auth, session)
            user = await accounts.create_user(f"rest-{uuid.uuid4().hex[:8]}", _PASSWORD)
            started = await accounts.start_session(user.id, user_agent="", ip=None)
            other = await accounts.create_user(f"rest-{uuid.uuid4().hex[:8]}", _PASSWORD)
            other_session = await accounts.start_session(other.id, user_agent="", ip=None)
            key = await accounts.create_key(user.id, "tests")
        client.cookies.set(SESSION_COOKIE, started.token)
        client.other_cookie = other_session.token
        client.api_key = key.value
        client.application = app
        async with Worker(
            services.temporal,
            task_queue=queue,
            workflows=[ChatWorkflow],
            activities=_chat_activities(settings, services.sessions),
        ):
            yield client


def _data(response: httpx2.Response, status: int = 200) -> dict[str, object]:
    assert response.status_code == status, response.text
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert isinstance(data, dict)
    return data


def _items(response: httpx2.Response) -> list[dict[str, object]]:
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["success"] is True
    assert "info" in body
    items = body["data"]
    assert isinstance(items, list)
    return items


def _error(response: httpx2.Response, status: int) -> None:
    assert response.status_code == status, response.text
    body = response.json()
    assert body["success"] is False
    assert isinstance(body["message"], str)


async def _chat(api: Api, text: str = "hello") -> dict[str, object]:
    return _data(await api.post("/api/v1/chats", json={"data": {"text": text}}), 201)


async def _pages(api: Api, path: str, per_page: int) -> list[list[UUID]]:
    pages: list[list[UUID]] = []
    page = 1
    while True:
        body = (await api.get(path, params={"per_page": per_page, "page": page})).json()
        pages.append([UUID(item["id"]) for item in body["data"]])
        if not body["info"]["has_next"]:
            return pages
        page += 1


async def test_readiness_checks_postgres_and_temporal(api: Api) -> None:
    response = await api.get("/api/v1/readyz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_requests_need_a_session_or_an_api_key(api: Api) -> None:
    anonymous = await api.get("/api/v1/chats", headers={"cookie": ""})
    wrong = await api.get("/api/v1/chats", headers={"authorization": "Bearer xv_nope_nope"})
    with_key = await api.get(
        "/api/v1/chats",
        headers={"cookie": "", "authorization": f"Bearer {api.api_key}"},
    )

    _error(anonymous, 401)
    _error(wrong, 401)
    assert with_key.status_code == 200


async def test_an_api_key_cannot_manage_sessions_or_keys(api: Api) -> None:
    headers = {"cookie": "", "authorization": f"Bearer {api.api_key}"}

    _error(await api.get("/api/v1/sessions", headers=headers), 403)
    _error(await api.post("/api/v1/api-keys", headers=headers, json={"data": {"name": "x"}}), 403)


async def test_sign_in_sets_the_cookie_and_a_wrong_password_is_rejected(api: Api) -> None:
    me = _data(await api.get("/api/v1/auth/me"))
    user = me["user"]
    assert isinstance(user, dict)
    username = user["username"]
    assert isinstance(username, str)

    signed_out = Api(
        transport=httpx2.ASGITransport(app=api.application), base_url="http://xavier.test"
    )
    async with signed_out:
        missing = await signed_out.patch("/api/v1/auth/me", json={"data": {"locale": "ru"}})
        rejected = await signed_out.post(
            "/api/v1/auth/session",
            json={"data": {"username": username, "password": "wrong-password"}},
        )
        accepted = await signed_out.post(
            "/api/v1/auth/session", json={"data": {"username": username, "password": _PASSWORD}}
        )

    _error(missing, 401)
    _error(rejected, 401)
    assert accepted.status_code == 201
    assert accepted.cookies.get(SESSION_COOKIE)
    assert user["locale"] is None

    invalid = await api.patch("/api/v1/auth/me", json={"data": {"locale": "de"}})
    updated = await api.patch("/api/v1/auth/me", json={"data": {"locale": "ru"}})

    _error(invalid, 422)
    updated_user = _data(updated)["user"]
    assert isinstance(updated_user, dict)
    assert updated_user["locale"] == "ru"


async def test_password_change_keeps_current_session_and_revokes_other_sessions(api: Api) -> None:
    me = _data(await api.get("/api/v1/auth/me"))
    user = me["user"]
    assert isinstance(user, dict)
    username = user["username"]
    assert isinstance(username, str)
    new_password = "new-long-enough-password"
    other = Api(transport=httpx2.ASGITransport(app=api.application), base_url="http://xavier.test")
    anonymous = Api(
        transport=httpx2.ASGITransport(app=api.application), base_url="http://xavier.test"
    )
    async with other, anonymous:
        signed_in = await other.post(
            "/api/v1/auth/session",
            json={"data": {"username": username, "password": _PASSWORD}},
        )
        assert signed_in.status_code == 201

        _error(
            await anonymous.patch(
                "/api/v1/auth/me/password",
                json={
                    "data": {
                        "current_password": _PASSWORD,
                        "new_password": new_password,
                    }
                },
            ),
            401,
        )
        _error(
            await api.patch(
                "/api/v1/auth/me/password",
                json={
                    "data": {
                        "current_password": "wrong-password",
                        "new_password": new_password,
                    }
                },
            ),
            422,
        )
        _error(
            await api.patch(
                "/api/v1/auth/me/password",
                json={
                    "data": {
                        "current_password": _PASSWORD,
                        "new_password": "too-short",
                    }
                },
            ),
            422,
        )

        changed = _data(
            await api.patch(
                "/api/v1/auth/me/password",
                json={
                    "data": {
                        "current_password": _PASSWORD,
                        "new_password": new_password,
                    }
                },
            )
        )
        assert changed["method"] == "session"
        assert (await api.get("/api/v1/auth/me")).status_code == 200
        _error(await other.get("/api/v1/auth/me"), 401)

        _error(
            await anonymous.post(
                "/api/v1/auth/session",
                json={"data": {"username": username, "password": _PASSWORD}},
            ),
            401,
        )
        assert (
            await anonymous.post(
                "/api/v1/auth/session",
                json={"data": {"username": username, "password": new_password}},
            )
        ).status_code == 201


async def test_an_api_key_cannot_change_the_account_password(api: Api) -> None:
    response = await api.patch(
        "/api/v1/auth/me/password",
        headers={"cookie": "", "authorization": f"Bearer {api.api_key}"},
        json={
            "data": {
                "current_password": _PASSWORD,
                "new_password": "api-key-new-password",
            }
        },
    )

    _error(response, 403)
    assert (await api.get("/api/v1/auth/me")).status_code == 200


async def test_a_revoked_session_stops_working(api: Api) -> None:
    sessions = _items(await api.get("/api/v1/sessions"))
    current = next(item for item in sessions if item["current"] is True)

    deleted = _data(await api.delete(f"/api/v1/sessions/{current['id']}"))

    assert deleted["revoked_at"] is not None
    _error(await api.get("/api/v1/auth/me"), 401)


async def test_chats_are_created_listed_newest_first_and_updated(api: Api) -> None:
    created = [UUID(str((await _chat(api, f"chat {n}"))["id"])) for n in range(3)]

    pages = await _pages(api, "/api/v1/chats", per_page=2)
    listed = [chat_id for page in pages for chat_id in page if chat_id in created]
    patched = await api.patch(f"/api/v1/chats/{created[0]}", json={"data": {"title": "Renamed"}})
    unknown_model = await api.patch(
        f"/api/v1/chats/{created[0]}", json={"data": {"model": "absent"}}
    )

    assert listed == created[::-1]
    assert all(len(page) <= 2 for page in pages)
    chat = _data(patched)
    assert chat["title"] == "Renamed"
    assert chat["computer_active"] is False
    _error(unknown_model, 422)
    _error(await api.get(f"/api/v1/chats/{uuid.uuid4()}"), 404)


async def test_another_users_chat_is_not_found(api: Api) -> None:
    chat_id = (await _chat(api))["id"]
    foreign = {"cookie": f"{SESSION_COOKIE}={api.other_cookie}"}

    _error(await api.get(f"/api/v1/chats/{chat_id}", headers=foreign), 404)
    _error(await api.get(f"/api/v1/chats/{chat_id}/messages", headers=foreign), 404)
    listed = _items(await api.get("/api/v1/chats", headers=foreign))
    assert chat_id not in {item["id"] for item in listed}


async def test_a_deleted_chat_disappears(api: Api) -> None:
    chat_id = (await _chat(api))["id"]

    deleted = _data(await api.delete(f"/api/v1/chats/{chat_id}"))

    assert deleted["deleted_at"] is not None
    _error(await api.get(f"/api/v1/chats/{chat_id}"), 404)
    listed = _items(await api.get("/api/v1/chats"))
    assert chat_id not in {item["id"] for item in listed}


async def test_chat_messages_turns_and_actions_are_listed(api: Api) -> None:
    chat_id = (await _chat(api, "first"))["id"]

    messages = _items(await api.get(f"/api/v1/chats/{chat_id}/messages"))
    turns = await api.get(f"/api/v1/chats/{chat_id}/turns")
    actions = await api.get(f"/api/v1/chats/{chat_id}/actions")

    assert [message["text"] for message in messages] == ["first"]
    assert turns.status_code == 200
    assert actions.status_code == 200
    _error(await api.get(f"/api/v1/chats/{chat_id}/turns/{uuid.uuid4()}"), 404)
    _error(await api.get(f"/api/v1/chats/{chat_id}/actions/{uuid.uuid4()}"), 404)


async def test_lists_reject_unknown_sort_and_filter_values(api: Api) -> None:
    chat_id = (await _chat(api))["id"]

    invalid_sort = await api.get("/api/v1/chats", params={"sort": "nope"})
    _error(invalid_sort, 422)
    assert invalid_sort.json()["details"][0]["loc"] == ["query", "sort"]
    _error(await api.get("/api/v1/chats", params={"direction": "sideways"}), 422)
    _error(await api.get("/api/v1/chats", params={"filter[status]": "imaginary"}), 422)
    _error(await api.get("/api/v1/chats", params={"per_page": 0}), 422)
    _error(await api.get(f"/api/v1/chats/{chat_id}/media", params={"filter[kind]": "video"}), 422)


async def test_catalogs_list_tools_and_models(api: Api) -> None:
    tools = _items(await api.get("/api/v1/tools"))
    models = _items(await api.get("/api/v1/models"))
    by_name = _items(await api.get("/api/v1/tools", params={"sort": "name", "direction": "asc"}))
    reverse = _items(await api.get("/api/v1/tools", params={"sort": "name", "direction": "desc"}))
    execute = _items(await api.get("/api/v1/tools", params={"filter[group]": "execute"}))

    names = [str(tool["name"]) for tool in tools]
    assert names[0] == "web_fetch"
    assert [str(tool["name"]) for tool in by_name] == sorted(names)
    assert [str(tool["name"]) for tool in reverse] == sorted(names, reverse=True)
    assert {str(tool["name"]) for tool in execute} == {"code_run", "job"}
    assert "code_run" in set(names)
    assert [(model["id"], model["default"]) for model in models] == [("test", True)]
    _error(await api.get("/api/v1/tools", params={"sort": "nope"}), 422)
    _error(await api.get("/api/v1/models", params={"direction": "sideways"}), 422)


_PAGED = (
    "/api/v1/chats",
    "/api/v1/approvals",
    "/api/v1/artifacts",
    "/api/v1/credentials",
    "/api/v1/policy/rules",
    "/api/v1/executions",
    "/api/v1/sources",
    "/api/v1/alerts",
    "/api/v1/subscriptions",
    "/api/v1/sessions",
    "/api/v1/api-keys",
    "/api/v1/schedules",
    "/api/v1/tools",
    "/api/v1/models",
)


async def test_paged_lists_reject_bad_sort_and_report_pages(api: Api) -> None:
    chat_id = (await _chat(api))["id"]
    paths = (
        *_PAGED,
        f"/api/v1/chats/{chat_id}/messages",
        f"/api/v1/chats/{chat_id}/events",
        f"/api/v1/chats/{chat_id}/turns",
        f"/api/v1/chats/{chat_id}/actions",
        f"/api/v1/chats/{chat_id}/wakeups",
        f"/api/v1/chats/{chat_id}/questions",
        f"/api/v1/chats/{chat_id}/media",
    )
    for path in paths:
        _error(await api.get(path, params={"sort": "nope"}), 422)
        _error(await api.get(path, params={"direction": "sideways"}), 422)
        page = (await api.get(path, params={"page": 1, "per_page": 1})).json()
        assert page["success"] is True, path
        info = page["info"]
        assert info["page"] == 1
        assert info["per_page"] == 1
        assert info["total_items"] >= len(page["data"])
        assert info["total_pages"] >= 0
        empty = (await api.get(path, params={"page": 100_000, "per_page": 1})).json()
        assert empty["data"] == [], path
        assert empty["info"]["has_next"] is False


async def test_a_cursor_from_another_chat_is_not_in_the_list(api: Api) -> None:
    first = (await _chat(api, "one"))["id"]
    second = (await _chat(api, "two"))["id"]
    message_id = _items(await api.get(f"/api/v1/chats/{first}/messages"))[0]["id"]

    foreign = await api.get(f"/api/v1/chats/{second}/messages", params={"cursor": str(message_id)})

    _error(foreign, 422)
    assert foreign.json()["details"][0]["loc"] == ["query", "cursor"]


async def test_metrics_summarize_a_period(api: Api) -> None:
    await _chat(api)
    end = datetime.now(UTC) + timedelta(minutes=1)

    summary = await api.get("/api/v1/metrics/summary", params={"end": end.isoformat()})
    reversed_period = await api.get(
        "/api/v1/metrics/summary",
        params={"start": end.isoformat(), "end": (end - timedelta(days=1)).isoformat()},
    )

    assert summary.status_code == 200
    assert summary.json()["data"]["chats"] >= 1
    _error(reversed_period, 422)


async def test_artifact_content_is_served_sandboxed(api: Api) -> None:
    uploaded = await api.post(
        "/api/v1/artifacts", files={"file": ("page.html", b"<script>1</script>", "text/html")}
    )
    artifact = _data(uploaded, 201)
    artifact_id = artifact["id"]

    content = await api.get(f"/api/v1/artifacts/{artifact_id}/content")
    listed = _items(await api.get("/api/v1/artifacts", params={"per_page": 1}))

    assert [item["id"] for item in listed] == [artifact_id]
    assert content.content == b"<script>1</script>"
    assert content.headers["content-security-policy"] == "default-src 'none'; sandbox"
    assert content.headers["content-disposition"].startswith("attachment")
    _error(await api.get(f"/api/v1/artifacts/{uuid.uuid4()}/content"), 404)


async def test_executions_are_accepted_and_paged(api: Api) -> None:
    accepted = [
        str(
            _data(
                await api.post("/api/v1/executions", json={"data": {"command": f"echo {n}"}}), 201
            )["id"]
        )
        for n in range(3)
    ]

    pages = await _pages(api, "/api/v1/executions", per_page=2)
    listed = [str(item) for page in pages for item in page]

    assert [item for item in listed if item in accepted] == accepted
    invalid = await api.post("/api/v1/executions", json={"data": {"code": "1", "command": "true"}})
    _error(invalid, 422)
    _error(await api.get(f"/api/v1/executions/{uuid.uuid4()}"), 404)


async def test_approvals_list_and_unknown_decision(api: Api) -> None:
    listed = await api.get("/api/v1/approvals")
    decision = await api.post(
        f"/api/v1/approvals/{uuid.uuid4()}/decision", json={"data": {"decision": "approve"}}
    )

    assert listed.status_code == 200
    _error(decision, 404)


async def test_policy_rules_crud_and_filters(api: Api) -> None:
    created = _data(
        await api.post(
            "/api/v1/policy/rules", json={"data": {"tool_pattern": "web_*", "effect": "ask"}}
        ),
        201,
    )
    rule_id = created["id"]

    updated = _data(
        await api.patch(
            f"/api/v1/policy/rules/{rule_id}",
            json={"data": {"tool_pattern": "web_*", "effect": "deny"}},
        )
    )
    global_rules = _items(await api.get("/api/v1/policy/rules", params={"filter[is_global]": True}))
    deleted = _data(await api.delete(f"/api/v1/policy/rules/{rule_id}"))

    assert updated["effect"] == "deny"
    assert rule_id in {item["id"] for item in global_rules}
    assert deleted["id"] == rule_id
    _error(await api.get(f"/api/v1/policy/rules/{rule_id}"), 404)


async def test_sources_alerts_and_subscriptions(api: Api) -> None:
    spec = {"url": "https://api.example.com/items", "schedule": {"interval": "PT1H"}}
    source = _data(
        await api.post("/api/v1/sources", json={"data": {"name": "items", "spec": spec}}), 201
    )

    listed = _items(await api.get("/api/v1/sources", params={"filter[status]": "draft"}))
    source_id = source["id"]
    assert isinstance(source_id, str)
    alerts = _items(await api.get("/api/v1/alerts", params={"filter[source_id]": source_id}))
    subscription = _data(
        await api.post(
            "/api/v1/subscriptions",
            json={
                "data": {
                    "channel": "webhook",
                    "url": "https://hooks.example.com/x",
                    "secret": "s" * 16,
                }
            },
        ),
        201,
    )
    incomplete = await api.post("/api/v1/subscriptions", json={"data": {"channel": "webhook"}})

    assert source["status"] == "draft"
    assert source["id"] in {item["id"] for item in listed}
    assert alerts == []
    assert "secret" not in subscription
    _error(incomplete, 422)
    _error(await api.post(f"/api/v1/sources/{source['id']}/check"), 409)
    _error(await api.post(f"/api/v1/alerts/{uuid.uuid4()}/resolve"), 404)


async def test_credentials_never_return_secrets_and_validate_hosts(api: Api) -> None:
    created = _data(
        await api.post(
            "/api/v1/credentials",
            json={
                "data": {
                    "name": "api",
                    "secret_type": "token",
                    "hosts": ["api.example.com"],
                    "value": "very-secret-key",
                }
            },
        ),
        201,
    )
    credential_id = created["id"]
    bad_host = await api.post(
        "/api/v1/credentials",
        json={
            "data": {
                "name": "bad",
                "secret_type": "token",
                "hosts": ["not a host"],
                "value": "x",
            }
        },
    )

    listed = await api.get("/api/v1/credentials")
    deleted = _data(await api.delete(f"/api/v1/credentials/{credential_id}"))

    assert "very-secret-key" not in created.__repr__() + listed.text
    assert deleted["id"] == credential_id
    _error(bad_host, 422)
    _error(await api.get(f"/api/v1/credentials/{credential_id}"), 404)


async def test_chat_schedules_crud(api: Api) -> None:
    created = _data(
        await api.post(
            "/api/v1/schedules",
            json={
                "data": {
                    "name": "daily",
                    "timing": {"cron": "0 9 * * *"},
                    "text": "report",
                    "paused": True,
                }
            },
        ),
        201,
    )
    schedule_id = created["id"]

    resumed = _data(await api.post(f"/api/v1/schedules/{schedule_id}/resume"))
    deleted = _data(await api.delete(f"/api/v1/schedules/{schedule_id}"))
    pinned_to_unknown = await api.post(
        "/api/v1/schedules",
        json={
            "data": {
                "name": "pinned",
                "timing": {"interval": "PT1H"},
                "text": "x",
                "chat_id": str(uuid.uuid4()),
            }
        },
    )

    assert created["paused"] is True
    assert resumed["paused"] is False
    assert deleted["id"] == schedule_id
    _error(await api.get(f"/api/v1/schedules/{schedule_id}"), 404)
    _error(pinned_to_unknown, 404)


def _request_trace(spans: InMemorySpanExporter, name: str) -> int:
    found = [span for span in spans.get_finished_spans() if span.name == name]
    assert found, [span.name for span in spans.get_finished_spans()]
    context = found[-1].context
    assert context is not None
    return context.trace_id


async def test_message_trace_reaches_the_chat_workflow(
    spans: InMemorySpanExporter, api: Api
) -> None:
    chat_id = (await _chat(api))["id"]
    spans.clear()

    response = await api.post(f"/api/v1/chats/{chat_id}/messages", json={"data": {"text": "again"}})

    assert response.status_code == 201, response.text
    trace_id = _request_trace(spans, "POST /api/v1/chats/{chat_id}/messages")
    temporal = [
        span
        for span in spans.get_finished_spans()
        if "ChatWorkflow" in span.name and span.context and span.context.trace_id == trace_id
    ]
    assert temporal


async def test_execution_trace_reaches_run_execution(
    spans: InMemorySpanExporter, api: Api, settings: Settings, s3: S3Client, queue: str
) -> None:
    client: Client = await connect_temporal(settings, s3)
    traces: dict[str, int] = {}
    ran = asyncio.Event()

    @activity.defn(name="run_execution")
    async def run_execution(execution_id: UUID) -> ExecutionDone:
        traces[str(execution_id)] = trace.get_current_span().get_span_context().trace_id
        ran.set()
        return ExecutionDone(status=ExecutionStatus.SUCCEEDED)

    @activity.defn(name="settle_execution")
    async def settle_execution(request: SettleExecution) -> ExecutionDone:
        raise AssertionError(request)

    async with Worker(
        client,
        task_queue=queue,
        workflows=[ExecutionWorkflow],
        activities=[run_execution, settle_execution],
    ):
        spans.clear()
        accepted = _data(
            await api.post("/api/v1/executions", json={"data": {"command": "true"}}), 201
        )
        execution_id = accepted["id"]
        request_trace = _request_trace(spans, "POST /api/v1/executions")

        async with asyncio.timeout(15):
            await ran.wait()

    assert traces == {execution_id: request_trace}
