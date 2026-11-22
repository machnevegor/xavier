"""Turns of a chat with their usage.

A turn is one agent pass over the input accumulated since the previous one: messages,
decisions and answers, or a wakeup or event in Proactive mode. Turns a message revision
replaced are superseded and listed only with `filter[include_superseded]=true`.
"""

from typing import Annotated
from uuid import UUID

from fastapi import Query
from sqlalchemy import select

from src.api.common import paginate, router
from src.api.dependencies import OwnedChatDep, SessionDep
from src.api.turns.schemas import ListTurnsInput, ListTurnsResult, ReadTurnResult, Turn, TurnsSort
from src.db import models as db
from src.errors import NotFoundError

router = router("/chats/{chat_id}/turns", "turns")

_SORT = {TurnsSort.ID: db.Turn.id, TurnsSort.STARTED_AT: db.Turn.started_at}


@router.get("")
async def list_turns(
    chat_id: UUID,
    params: Annotated[ListTurnsInput, Query()],
    _: OwnedChatDep,
    session: SessionDep,
) -> ListTurnsResult:
    """List the chat turns with their usage, oldest first by default."""
    query = select(db.Turn).where(db.Turn.chat_id == chat_id)
    if params.status:
        query = query.where(db.Turn.status.in_(params.status))
    if params.trigger:
        query = query.where(db.Turn.trigger.in_(params.trigger))
    if not params.include_superseded:
        query = query.where(db.Turn.superseded_at.is_(None))
    rows, info = await paginate(
        session, query, params, sort=_SORT[params.sort], direction=params.direction, key=db.Turn.id
    )
    return ListTurnsResult(data=[Turn.model_validate(row) for row in rows], info=info)


@router.get("/{turn_id}")
async def read_turn(
    chat_id: UUID, turn_id: UUID, _: OwnedChatDep, session: SessionDep
) -> ReadTurnResult:
    """Return a turn with its outcome and usage."""
    turn = await session.get(db.Turn, turn_id)
    if turn is None or turn.chat_id != chat_id:
        raise NotFoundError(f"turn {turn_id} not found")
    return ReadTurnResult(data=Turn.model_validate(turn))
