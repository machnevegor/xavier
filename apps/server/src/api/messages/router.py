"""Operator messages: sending, listing, revising and regenerating.

A message sent while a turn runs, while the chat waits for approvals or while it is paused is
queued for the next turn. Images and files are uploaded first with `POST /artifacts` and
attached by id. A 503 means the message is stored but the agent was not notified; another
message or a command resumes it.

A revision replaces a message and lets the agent answer it again: a running turn is stopped
and the agent continues from its state before the message. Later turns and messages are
superseded; they stay readable with `filter[include_superseded]=true`. Their completed side
effects are not undone; the agent is told which ones happened.
"""

from typing import Annotated
from uuid import UUID

from fastapi import Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.common import CONFLICT, INVALID_REQUEST, UNAVAILABLE, paginate, router
from src.api.dependencies import OwnedChatDep, ServicesDep, SessionDep
from src.api.messages.schemas import (
    CreateMessageArgs,
    CreateMessageResult,
    ListMessagesInput,
    ListMessagesResult,
    Message,
    MessagesSort,
    RegenerateMessageResult,
    ReviseMessageArgs,
    ReviseMessageResult,
)
from src.api.messages.service import add_message, claim_artifacts
from src.db import models as db
from src.errors import ConflictError, NotFoundError
from src.orchestration.client import ChatCommands
from src.orchestration.models import EditMessage

router = router("/chats/{chat_id}/messages", "messages")

_SORT = {MessagesSort.ID: db.Message.id, MessagesSort.CREATED_AT: db.Message.created_at}


@router.post(
    "", status_code=status.HTTP_201_CREATED, responses={422: INVALID_REQUEST, 503: UNAVAILABLE}
)
async def create_message(
    chat_id: UUID,
    args: CreateMessageArgs,
    chat: OwnedChatDep,
    session: SessionDep,
    services: ServicesDep,
) -> CreateMessageResult:
    """Send a message to the agent."""
    message = await add_message(session, chat_id, chat.created_by, args.data)
    await session.commit()
    await services.commands.input_available(chat_id)
    return CreateMessageResult(data=Message.model_validate(message))


@router.get("")
async def list_messages(
    chat_id: UUID,
    params: Annotated[ListMessagesInput, Query()],
    _: OwnedChatDep,
    session: SessionDep,
) -> ListMessagesResult:
    """List the chat messages, oldest first by default."""
    query = select(db.Message).where(db.Message.chat_id == chat_id)
    if not params.include_superseded:
        query = query.where(db.Message.status != db.MessageStatus.SUPERSEDED)
    rows, info = await paginate(
        session,
        query,
        params,
        sort=_SORT[params.sort],
        direction=params.direction,
        key=db.Message.id,
    )
    return ListMessagesResult(data=[Message.model_validate(row) for row in rows], info=info)


@router.post(
    "/{message_id}/revisions",
    status_code=status.HTTP_201_CREATED,
    responses={409: CONFLICT, 422: INVALID_REQUEST, 503: UNAVAILABLE},
)
async def revise_message(  # noqa: PLR0913 - the parts of one request
    *,
    chat_id: UUID,
    message_id: UUID,
    args: ReviseMessageArgs,
    chat: OwnedChatDep,
    session: SessionDep,
    services: ServicesDep,
) -> ReviseMessageResult:
    """Replace an operator message with a revision and let the agent answer it again.

    Returns the queued revision. 409 when the message is not a current operator message or
    the history before it is no longer kept.
    """
    await _check_revisable(session, chat_id, message_id)
    await claim_artifacts(session, chat_id, chat.created_by, args.data)
    await session.commit()
    edit = EditMessage(
        message_id=message_id,
        text=args.data.text,
        image_artifact_ids=args.data.image_artifact_ids,
        file_artifact_ids=args.data.file_artifact_ids,
    )
    return ReviseMessageResult(data=await _revise(session, services.commands, chat_id, edit))


@router.post(
    "/{message_id}/regenerate",
    status_code=status.HTTP_201_CREATED,
    responses={409: CONFLICT, 503: UNAVAILABLE},
)
async def regenerate_message(
    chat_id: UUID,
    message_id: UUID,
    _: OwnedChatDep,
    session: SessionDep,
    services: ServicesDep,
) -> RegenerateMessageResult:
    """Send an operator message again unchanged and let the agent answer it anew.

    Works like a revision with the same content.
    """
    await _check_revisable(session, chat_id, message_id)
    await session.commit()
    edit = EditMessage(message_id=message_id)
    return RegenerateMessageResult(data=await _revise(session, services.commands, chat_id, edit))


async def _check_revisable(session: AsyncSession, chat_id: UUID, message_id: UUID) -> None:
    message = await session.get(db.Message, message_id)
    if message is None or message.chat_id != chat_id:
        raise NotFoundError(f"message {message_id} not found")
    if message.kind is not db.MessageKind.USER or message.status is db.MessageStatus.SUPERSEDED:
        raise ConflictError("only current operator messages can be revised")


async def _revise(
    session: AsyncSession, commands: ChatCommands, chat_id: UUID, edit: EditMessage
) -> Message:
    rewound = await commands.edit_message(chat_id, edit)
    if rewound.message_id is None:
        raise ConflictError(rewound.error)
    message = await session.get_one(db.Message, rewound.message_id, populate_existing=True)
    return Message.model_validate(message)
