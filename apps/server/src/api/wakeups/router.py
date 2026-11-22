"""Wakeups of a chat.

In Proactive mode the agent may schedule a turn for later with a reason. A pending wakeup can
be cancelled; turning Proactive mode off cancels every pending wakeup of the chat.
"""

from typing import Annotated
from uuid import UUID

from fastapi import Query
from sqlalchemy import select

from src.agent import proactive
from src.api.common import paginate, router
from src.api.dependencies import OwnedChatDep, SessionDep
from src.api.wakeups.schemas import (
    DeleteWakeupResult,
    ListWakeupsInput,
    ListWakeupsResult,
    Wakeup,
    WakeupsSort,
)
from src.db import models as db
from src.errors import NotFoundError

router = router("/chats/{chat_id}/wakeups", "wakeups")

_SORT = {
    WakeupsSort.ID: db.Wakeup.id,
    WakeupsSort.CREATED_AT: db.Wakeup.created_at,
    WakeupsSort.DUE_AT: db.Wakeup.due_at,
}


@router.get("")
async def list_wakeups(
    chat_id: UUID,
    params: Annotated[ListWakeupsInput, Query()],
    _: OwnedChatDep,
    session: SessionDep,
) -> ListWakeupsResult:
    """List the wakeups the agent scheduled, oldest first by default."""
    query = select(db.Wakeup).where(
        db.Wakeup.chat_id == chat_id, db.Wakeup.status.in_(params.status)
    )
    rows, info = await paginate(
        session,
        query,
        params,
        sort=_SORT[params.sort],
        direction=params.direction,
        key=db.Wakeup.id,
    )
    return ListWakeupsResult(data=[Wakeup.model_validate(row) for row in rows], info=info)


@router.delete("/{wakeup_id}")
async def delete_wakeup(
    chat_id: UUID, wakeup_id: UUID, _: OwnedChatDep, session: SessionDep
) -> DeleteWakeupResult:
    """Cancel a pending wakeup; returns it cancelled."""
    cancelled = await proactive.cancel(session, chat_id, db.Wakeup.id == wakeup_id)
    if not cancelled:
        raise NotFoundError(f"pending wakeup {wakeup_id} not found")
    return DeleteWakeupResult(data=Wakeup.model_validate(cancelled[0]))
