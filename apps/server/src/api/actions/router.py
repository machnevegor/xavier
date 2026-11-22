"""Tool calls of a chat.

Every tool call the agent makes is an action: the guardrails moderate it, the operator may
decide on it, and it ends with an outcome. Arguments are shown with secrets redacted.
"""

from typing import Annotated
from uuid import UUID

from fastapi import Query
from sqlalchemy import select

from src.api.actions.schemas import (
    Action,
    ActionsSort,
    ListActionsInput,
    ListActionsResult,
    ReadActionResult,
)
from src.api.common import paginate, router
from src.api.dependencies import OwnedChatDep, SessionDep
from src.db import models as db
from src.errors import NotFoundError

router = router("/chats/{chat_id}/actions", "actions")

_SORT = {ActionsSort.ID: db.Action.id, ActionsSort.CREATED_AT: db.Action.created_at}


@router.get("")
async def list_actions(
    chat_id: UUID,
    params: Annotated[ListActionsInput, Query()],
    _: OwnedChatDep,
    session: SessionDep,
) -> ListActionsResult:
    """List the chat tool calls, oldest first by default."""
    query = select(db.Action).where(db.Action.chat_id == chat_id)
    if params.turn_id:
        query = query.where(db.Action.turn_id.in_(params.turn_id))
    if params.status:
        query = query.where(db.Action.status.in_(params.status))
    rows, info = await paginate(
        session,
        query,
        params,
        sort=_SORT[params.sort],
        direction=params.direction,
        key=db.Action.id,
    )
    return ListActionsResult(data=[Action.model_validate(row) for row in rows], info=info)


@router.get("/{action_id}")
async def read_action(
    chat_id: UUID, action_id: UUID, _: OwnedChatDep, session: SessionDep
) -> ReadActionResult:
    """Return a tool call with its moderation and outcome."""
    action = await session.get(db.Action, action_id)
    if action is None or action.chat_id != chat_id:
        raise NotFoundError(f"action {action_id} not found")
    return ReadActionResult(data=Action.model_validate(action))
