"""Files: uploads and the outputs of tools, runs and agent answers.

An artifact either belongs to a chat or stands alone, such as an upload outside chats or the
output of a code run. Chat artifacts are visible while their chat is; artifacts of a deleted
chat, and another user's artifacts, do not exist.
"""

from typing import Annotated
from urllib.parse import quote
from uuid import UUID

from fastapi import Depends, Form, Query, Response, UploadFile, status
from sqlalchemy import ColumnElement, and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.artifacts.schemas import (
    Artifact,
    ArtifactKind,
    ArtifactsSort,
    CreateArtifactResult,
    ListArtifactsInput,
    ListArtifactsResult,
    ReadArtifactResult,
)
from src.api.common import NOT_FOUND, paginate, router
from src.api.dependencies import CurrentUserDep, ServicesDep, SessionDep
from src.api.media.router import IMAGE_TYPES
from src.db import models as db
from src.errors import NotFoundError, PayloadTooLargeError
from src.storage.artifacts import MAX_UPLOAD_BYTES, artifact_key

router = router("/artifacts", "artifacts")

_CHUNK = 1024 * 1024
_SORT = {
    ArtifactsSort.ID: db.Artifact.id,
    ArtifactsSort.CREATED_AT: db.Artifact.created_at,
    ArtifactsSort.NAME: db.Artifact.name,
    ArtifactsSort.SIZE_BYTES: db.Artifact.size_bytes,
}


def _owned_by(owner: UUID) -> ColumnElement[bool]:
    """Return whether an artifact is the owner's: outside chats, or of their non-deleted chat."""
    chats = select(db.Chat.id).where(db.Chat.created_by == owner, db.Chat.deleted_at.is_(None))
    return or_(
        and_(db.Artifact.chat_id.is_(None), db.Artifact.created_by == owner),
        db.Artifact.chat_id.in_(chats),
    )


async def owned_artifact(session: AsyncSession, artifact_id: UUID, owner: UUID) -> db.Artifact:
    """Return the owner's artifact.

    Raises
    ------
    NotFoundError
        If the owner has no such artifact.
    """
    artifact = await session.scalar(
        select(db.Artifact).where(db.Artifact.id == artifact_id, _owned_by(owner))
    )
    if artifact is None:
        raise NotFoundError(f"artifact {artifact_id} not found")
    return artifact


async def get_owned_artifact(
    artifact_id: UUID, principal: CurrentUserDep, session: SessionDep
) -> db.Artifact:
    """Return the user's artifact of the path."""
    return await owned_artifact(session, artifact_id, principal.user_id)


OwnedArtifactDep = Annotated[db.Artifact, Depends(get_owned_artifact)]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_artifact(
    file: UploadFile,
    principal: CurrentUserDep,
    session: SessionDep,
    services: ServicesDep,
    chat_id: Annotated[
        UUID | None, Form(description="_Optional_. Chat the file belongs to")
    ] = None,
) -> CreateArtifactResult:
    """Upload a file, for example an image to attach to a message (64 MiB at most)."""
    if chat_id is not None:
        chat = await session.get(db.Chat, chat_id)
        if chat is None or chat.created_by != principal.user_id or chat.deleted_at is not None:
            raise NotFoundError(f"chat {chat_id} not found")
    data = bytearray()
    while chunk := await file.read(_CHUNK):
        data.extend(chunk)
        if len(data) > MAX_UPLOAD_BYTES:
            raise PayloadTooLargeError(f"the limit is {MAX_UPLOAD_BYTES} bytes")
    artifact = await services.artifacts.create(
        chat_id=chat_id,
        name=file.filename or "upload",
        content_type=file.content_type or "application/octet-stream",
        data=bytes(data),
        source="upload",
        created_by=principal.user_id,
    )
    return CreateArtifactResult(data=Artifact.model_validate(artifact))


@router.get("")
async def list_artifacts(
    principal: CurrentUserDep, session: SessionDep, params: Annotated[ListArtifactsInput, Query()]
) -> ListArtifactsResult:
    """List the user's artifacts, newest first by default."""
    query = select(db.Artifact).where(_owned_by(principal.user_id))
    if params.chat_id:
        query = query.where(db.Artifact.chat_id.in_(params.chat_id))
    if params.kind is ArtifactKind.IMAGE:
        query = query.where(db.Artifact.content_type.in_(IMAGE_TYPES))
    elif params.kind is ArtifactKind.FILE:
        query = query.where(db.Artifact.content_type.not_in(IMAGE_TYPES))
    rows, info = await paginate(
        session,
        query,
        params,
        sort=_SORT[params.sort],
        direction=params.direction,
        key=db.Artifact.id,
    )
    return ListArtifactsResult(data=[Artifact.model_validate(row) for row in rows], info=info)


@router.get("/{artifact_id}", responses={404: NOT_FOUND})
async def read_artifact(artifact: OwnedArtifactDep) -> ReadArtifactResult:
    """Return the artifact metadata."""
    return ReadArtifactResult(data=Artifact.model_validate(artifact))


@router.get(
    "/{artifact_id}/content",
    response_class=Response,
    responses={200: {"content": {"application/octet-stream": {}}}, 404: NOT_FOUND},
)
async def read_artifact_content(artifact: OwnedArtifactDep, services: ServicesDep) -> Response:
    """Download the artifact with its media type.

    PNG, JPEG, GIF and WebP images are shown inline; other files are attachments.
    """
    data = await services.blobs.get(artifact_key(artifact.id))
    disposition = "inline" if artifact.content_type in IMAGE_TYPES else "attachment"
    return Response(
        data,
        media_type=artifact.content_type,
        headers={
            "Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(artifact.name)}",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "X-Content-Type-Options": "nosniff",
        },
    )
