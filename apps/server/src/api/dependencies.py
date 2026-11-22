"""Dependencies of request handlers: services, the request session, accounts and the principal.

Every request gets one database session; it commits when the handler returns and rolls back
when it raises. A handler that must publish its writes before notifying a workflow commits
explicitly. Event streams and WebSockets outlive the handler and open short sessions of their
own through `authenticate` and `still_active`.
"""

from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

import httpx2
from fastapi import Depends
from fastapi.security import APIKeyCookie, HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import HTTPConnection
from temporalio.client import Client

from src.auth.service import Accounts, Principal
from src.config import Settings
from src.db import models as db
from src.db.engine import Sessions
from src.errors import ForbiddenError, NotFoundError, UnauthorizedError
from src.events import Listener
from src.execute.service import Executions
from src.leases import Leases
from src.observe.credentials import Vault
from src.observe.fetch import HttpFetcher
from src.observe.service import Sources
from src.orchestration.client import ChatCommands
from src.orchestration.schedules import ChatSchedules, SourceSchedules
from src.storage.artifacts import Artifacts
from src.storage.blobs import BlobStore

SESSION_COOKIE = "xavier_session"
STREAM_CHECK_S = 15.0
"""Seconds between checks that the principal of an open stream is still signed in."""


@dataclass(frozen=True, slots=True)
class Services:
    """Connections and services opened in the application lifespan."""

    settings: Settings
    sessions: Sessions
    temporal: Client
    artifacts: Artifacts
    commands: ChatCommands
    executions: Executions
    events: Listener
    """``LISTEN xv_events`` for event streams."""
    workers: httpx2.AsyncClient
    """Client of worker internal servers."""
    leases: Leases
    blobs: BlobStore
    fetcher: HttpFetcher
    """Pinned public fetches of external images in agent answers."""
    sources: Sources
    vault: Vault
    source_schedules: SourceSchedules
    chat_schedules: ChatSchedules


def get_services(connection: HTTPConnection) -> Services:
    """Return the application services."""
    services: Services = connection.app.state.services
    return services


ServicesDep = Annotated[Services, Depends(get_services)]


async def get_session(services: ServicesDep) -> AsyncGenerator[AsyncSession]:
    """Yield the request's session in a transaction that ends before the response is sent."""
    async with services.sessions.begin() as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_session, scope="function")]


def get_accounts(services: ServicesDep, session: SessionDep) -> Accounts:
    """Return the accounts of the request's session."""
    return Accounts(services.settings.auth, session)


AccountsDep = Annotated[Accounts, Depends(get_accounts)]

_bearer = HTTPBearer(
    auto_error=False, description="An API key `xv_…` created under Account → API keys."
)
_cookie = APIKeyCookie(
    name=SESSION_COOKIE, auto_error=False, description="The session cookie set by sign-in."
)


def credential(connection: HTTPConnection) -> str | None:
    """Return the API key of the ``Authorization`` header, else the session cookie value."""
    scheme, _, value = connection.headers.get("authorization", "").partition(" ")
    if scheme.lower() == "bearer" and value:
        return value.strip()
    return connection.cookies.get(SESSION_COOKIE) or None


async def get_principal(
    connection: HTTPConnection,
    accounts: AccountsDep,
    _bearer_value: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    _cookie_value: Annotated[str | None, Depends(_cookie)],
) -> Principal:
    """Return who makes the request: a valid session cookie or API key."""
    value = credential(connection)
    principal = await accounts.resolve(value) if value else None
    if principal is None:
        raise UnauthorizedError("no valid session or API key")
    return principal


CurrentUserDep = Annotated[Principal, Depends(get_principal)]


def get_browser_principal(principal: CurrentUserDep) -> Principal:
    """Return the principal if it signed in with a browser session."""
    if principal.session_id is None:
        raise ForbiddenError("sessions and API keys are managed from a signed-in browser")
    return principal


BrowserUserDep = Annotated[Principal, Depends(get_browser_principal)]


async def get_owned_chat(chat_id: UUID, principal: CurrentUserDep, session: SessionDep) -> db.Chat:
    """Return the user's chat; another user's or a deleted chat does not exist."""
    chat = await session.get(db.Chat, chat_id)
    if chat is None or chat.created_by != principal.user_id or chat.deleted_at is not None:
        raise NotFoundError(f"chat {chat_id} not found")
    return chat


OwnedChatDep = Annotated[db.Chat, Depends(get_owned_chat)]


async def chat_visible(session: AsyncSession, chat_id: UUID | None, owner: UUID) -> bool:
    """Return whether ``chat_id`` is none or a chat of ``owner`` that is not deleted."""
    if chat_id is None:
        return True
    chat = await session.get(db.Chat, chat_id)
    return chat is not None and chat.created_by == owner and chat.deleted_at is None


async def authenticate(connection: HTTPConnection, services: Services) -> Principal | None:
    """Return the principal of a long-lived connection, resolved in a session of its own."""
    value = credential(connection)
    if value is None:
        return None
    async with services.sessions.begin() as session:
        return await Accounts(services.settings.auth, session).resolve(value)


async def get_stream_principal(
    connection: HTTPConnection,
    services: ServicesDep,
    _bearer_value: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    _cookie_value: Annotated[str | None, Depends(_cookie)],
) -> Principal:
    """Return the principal of an event stream.

    The session that resolves the principal closes before the stream starts. A request session
    would stay checked out for the life of the connection.
    """
    principal = await authenticate(connection, services)
    if principal is None:
        raise UnauthorizedError("no valid session or API key")
    return principal


StreamUserDep = Annotated[Principal, Depends(get_stream_principal)]


async def still_active(services: Services, principal: Principal) -> bool:
    """Return whether the principal of an open stream is still signed in."""
    async with services.sessions() as session:
        return await Accounts(services.settings.auth, session).active(principal)
