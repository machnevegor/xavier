"""Signed-in devices of the user.

Only sessions that can still be used are listed: not signed out, not expired and not idle
for longer than the configured idle time. Sessions are managed only from a signed-in browser;
requests with an API key get 403.
"""

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import Query
from sqlalchemy import select

from src.api.common import NOT_FOUND, paginate, router
from src.api.dependencies import AccountsDep, BrowserUserDep, ServicesDep, SessionDep
from src.api.sessions.schemas import (
    DeleteSessionResult,
    ListSessionsInput,
    ListSessionsResult,
    Session,
    SessionsSort,
)
from src.auth.service import Principal, live_sessions
from src.db import models as db

router = router("/sessions", "sessions")

_SORT = {
    SessionsSort.CREATED_AT: db.AuthSession.created_at,
    SessionsSort.LAST_SEEN_AT: db.AuthSession.last_seen_at,
    SessionsSort.EXPIRES_AT: db.AuthSession.expires_at,
}


@router.get("")
async def list_sessions(
    params: Annotated[ListSessionsInput, Query()],
    principal: BrowserUserDep,
    session: SessionDep,
    services: ServicesDep,
) -> ListSessionsResult:
    """List the user's sessions; `current` marks the one of this request."""
    query = select(db.AuthSession).where(
        db.AuthSession.user_id == principal.user_id,
        live_sessions(services.settings.auth, datetime.now(UTC)),
    )
    rows, info = await paginate(
        session,
        query,
        params,
        sort=_SORT[params.sort],
        direction=params.direction,
        key=db.AuthSession.id,
    )
    return ListSessionsResult(data=[_session(row, principal) for row in rows], info=info)


@router.delete("/{session_id}", responses={404: NOT_FOUND})
async def delete_session(
    session_id: UUID, principal: BrowserUserDep, accounts: AccountsDep
) -> DeleteSessionResult:
    """Sign out a session; revoking the current one signs this browser out."""
    revoked = await accounts.revoke_session(principal.user_id, session_id)
    return DeleteSessionResult(data=_session(revoked, principal))


def _session(row: db.AuthSession, principal: Principal) -> Session:
    return Session(
        id=row.id,
        user_agent=row.user_agent,
        ip=row.ip,
        current=row.id == principal.session_id,
        created_at=row.created_at,
        last_seen_at=row.last_seen_at,
        expires_at=row.expires_at,
        revoked_at=row.revoked_at,
    )
