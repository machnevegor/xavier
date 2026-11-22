"""Shared fakes for unit and integration tests that build a ``Services`` bag."""

import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Never, cast
from uuid import UUID

from pydantic import JsonValue

from src.compute.service import ComputeService
from src.config import Settings
from src.credentials.service import AgentCredentials
from src.db.engine import Sessions
from src.db.models import Artifact
from src.execute.service import Executions
from src.leases import Leases
from src.observe.fetch import BrowserRenderer, HttpFetcher
from src.observe.service import Sources
from src.storage.artifacts import Artifacts
from src.tools.base import Services
from src.workspace.service import WorkspaceService


class NoSessions:
    """A session factory that refuses to open a database."""

    @asynccontextmanager
    async def __call__(self) -> AsyncGenerator[Never]:
        raise AssertionError("no database")
        yield  # pragma: no cover

    @asynccontextmanager
    async def begin(self) -> AsyncGenerator[Never]:
        raise AssertionError("no database")
        yield  # pragma: no cover


class NoCredentials(AgentCredentials):
    def __init__(self) -> None:
        pass

    async def available(self, chat_id: UUID, owner: UUID) -> list[dict[str, JsonValue]]:
        del chat_id, owner
        return []


class NoArtifacts(Artifacts):
    def __init__(self) -> None:
        self.created: list[bytes] = []

    async def create(  # noqa: PLR0913 - mirrors Artifacts.create
        self,
        *,
        chat_id: UUID | None,
        name: str,
        content_type: str,
        data: bytes,
        source: str,
        created_by: UUID | None = None,
    ) -> Artifact:
        del content_type, source
        self.created.append(data)
        return Artifact(id=uuid.uuid7(), chat_id=chat_id, name=name, size_bytes=len(data))


class NoWorkspaces(WorkspaceService):
    def __init__(self) -> None:
        pass


class NoExecutions(Executions):
    def __init__(self) -> None:
        pass


class NoLeases(Leases):
    def __init__(self) -> None:
        pass


class NoRenderer(BrowserRenderer):
    def __init__(self) -> None:
        pass


class NoSources(Sources):
    def __init__(self) -> None:
        pass


def test_settings(**values: object) -> Settings:
    """Return minimal settings; ``values`` override top-level sections or fields."""
    base: dict[str, object] = {
        "worker": {"internal_token": "t"},
        "database": {"password": "p"},
        "s3": {"access_key_id": "a", "secret_access_key": "s"},
        "observe": {"credentials_key": "vBEIpEgikO_ICioUfkcoQ2wQc9mtSBVhuxtZW05nJqM="},
        "agent": {
            "providers": {"p": {"base_url": "http://llm", "api_key": "k"}},
            "models": [
                {
                    "id": "m",
                    "provider": "p",
                    "provider_model": "m",
                    "pricing_per_mtok": {"input": 1, "output": 1},
                }
            ],
            "default_model": "m",
        },
    }
    base.update(values)
    return Settings.model_validate(base)


def services(  # noqa: PLR0913 - the bag mirrors production wiring
    *,
    settings: Settings | None = None,
    sessions: Sessions | None = None,
    artifacts: Artifacts | None = None,
    fetcher: HttpFetcher | None = None,
    renderer: BrowserRenderer | None = None,
    sources: Sources | None = None,
    workspaces: WorkspaceService | None = None,
    executions: Executions | None = None,
    compute: ComputeService | None = None,
    credentials: AgentCredentials | None = None,
    host_queue: str = "xavier.host.test",
) -> Services:
    """Return a ``Services`` bag with no-op defaults for unused collaborators."""
    return Services(
        settings=settings or test_settings(),
        sessions=sessions if sessions is not None else cast(Sessions, NoSessions()),
        artifacts=artifacts or NoArtifacts(),
        fetcher=fetcher or HttpFetcher(),
        renderer=renderer or NoRenderer(),
        sources=sources or NoSources(),
        workspaces=workspaces or NoWorkspaces(),
        executions=NoExecutions() if executions is None else executions,
        compute=compute or ComputeService(NoLeases()),
        credentials=credentials or NoCredentials(),
        host_queue=host_queue,
    )
