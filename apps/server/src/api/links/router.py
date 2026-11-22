"""Deep links to a message, action, question or artifact of a chat.

A link names an object by kind and id; resolving it returns the chat and turn that show it.
Objects of another user's or a deleted chat, and artifacts outside chats, do not resolve.
"""

from typing import Annotated

from fastapi import Query
from sqlalchemy import func, select

from src.api.common import router
from src.api.dependencies import CurrentUserDep, SessionDep
from src.api.links.schemas import Link, LinkKind, ResolveLinkInput, ResolveLinkResult
from src.db import models as db
from src.errors import NotFoundError

router = router("/links", "links")

_MODELS: dict[LinkKind, type[db.Message | db.Action | db.Question | db.Artifact]] = {
    LinkKind.MESSAGE: db.Message,
    LinkKind.ACTION: db.Action,
    LinkKind.QUESTION: db.Question,
    LinkKind.ARTIFACT: db.Artifact,
}


@router.get("/resolve")
async def resolve_link(
    principal: CurrentUserDep, session: SessionDep, params: Annotated[ResolveLinkInput, Query()]
) -> ResolveLinkResult:
    """Return the chat and turn that show the object.

    A message also gets its position among the chat's current messages.
    """
    row = await session.get(_MODELS[params.kind], params.id)
    chat = (
        await session.get(db.Chat, row.chat_id)
        if row is not None and row.chat_id is not None
        else None
    )
    if (
        row is None
        or chat is None
        or chat.created_by != principal.user_id
        or chat.deleted_at is not None
    ):
        raise NotFoundError(f"{params.kind} {params.id} not found in a chat")
    position = None
    if isinstance(row, db.Message):
        position = await session.scalar(
            select(func.count()).where(
                db.Message.chat_id == chat.id,
                db.Message.status != db.MessageStatus.SUPERSEDED,
                db.Message.id < row.id,
            )
        )
    return ResolveLinkResult(
        data=Link(
            kind=params.kind,
            id=params.id,
            chat_id=chat.id,
            turn_id=None if isinstance(row, db.Artifact) else row.turn_id,
            position=position,
        )
    )
