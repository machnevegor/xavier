"""Queueing operator messages: shared by chat creation and new messages."""

import uuid
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.api.messages.schemas import CreateMessageInput
from src.db import models as db
from src.errors import InvalidRequestError, NotFoundError
from src.events import MESSAGES_CHANNEL, notify


async def claim_artifacts(
    session: AsyncSession, chat_id: UUID, owner: UUID, data: CreateMessageInput
) -> None:
    """Attach the message's artifacts to the chat.

    Each artifact must belong to ``owner`` and be unattached or attached to the chat already.
    """
    images = set(data.image_artifact_ids)
    for artifact_id in [*data.image_artifact_ids, *data.file_artifact_ids]:
        artifact = await session.get(db.Artifact, artifact_id)
        if (
            artifact is None
            or artifact.created_by != owner
            or artifact.chat_id not in {None, chat_id}
        ):
            raise NotFoundError(f"artifact {artifact_id} not found")
        if artifact_id in images and not artifact.content_type.startswith("image/"):
            raise InvalidRequestError(f"artifact {artifact_id} is not an image")
        artifact.chat_id = chat_id


async def add_message(
    session: AsyncSession, chat_id: UUID, owner: UUID, data: CreateMessageInput
) -> db.Message:
    """Queue the message and wake a running turn of the chat, which yields to it."""
    await claim_artifacts(session, chat_id, owner, data)
    message = db.Message(
        id=uuid.uuid7(),
        chat_id=chat_id,
        text=data.text,
        image_artifact_ids=data.image_artifact_ids,
        file_artifact_ids=data.file_artifact_ids,
        status=db.MessageStatus.QUEUED,
    )
    session.add(message)
    await session.flush()
    await session.refresh(message)
    await notify(session, MESSAGES_CHANNEL, str(chat_id))
    return message
