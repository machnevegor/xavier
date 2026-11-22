"""Artifacts: file metadata in PostgreSQL, content in S3 under ``artifacts/<id>``."""

import hashlib
import uuid
from uuid import UUID

from sqlalchemy import select

from src.db.engine import Sessions
from src.db.models import Artifact, Chat
from src.errors import NotFoundError
from src.storage.blobs import BlobStore

MAX_UPLOAD_BYTES = 64 * 1024 * 1024


def artifact_key(artifact_id: UUID) -> str:
    """Return the S3 key of the artifact content."""
    return f"artifacts/{artifact_id}"


class Artifacts:
    """Create and read artifacts."""

    def __init__(self, sessions: Sessions, blobs: BlobStore) -> None:
        self._sessions = sessions
        self._blobs = blobs

    async def create(  # noqa: PLR0913 - the fields of an artifact
        self,
        *,
        chat_id: UUID | None,
        name: str,
        content_type: str,
        data: bytes,
        source: str,
        created_by: UUID | None = None,
    ) -> Artifact:
        """Store ``data`` and its metadata; the content is written first.

        The owner is the chat's owner; an artifact outside chats needs ``created_by``.
        """
        if chat_id is None and created_by is None:
            raise ValueError("an artifact outside chats needs its owner")
        artifact = Artifact(
            id=uuid.uuid7(),
            chat_id=chat_id,
            name=name,
            content_type=content_type,
            size_bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            source=source,
        )
        await self._blobs.put(artifact_key(artifact.id), data, content_type=content_type)
        async with self._sessions.begin() as session:
            owner = created_by
            if chat_id is not None:
                owner = await session.scalar(select(Chat.created_by).where(Chat.id == chat_id))
            if owner is None:
                raise NotFoundError(f"chat {chat_id} not found")
            artifact.created_by = owner
            session.add(artifact)
            await session.flush()
            await session.refresh(artifact)
        return artifact

    async def create_once(
        self, *, chat_id: UUID, name: str, content_type: str, data: bytes, source: str
    ) -> Artifact:
        """Return the chat's artifact with this name and content, creating it if missing."""
        digest = hashlib.sha256(data).hexdigest()
        async with self._sessions() as session:
            existing = await session.scalar(
                select(Artifact)
                .where(
                    Artifact.chat_id == chat_id,
                    Artifact.name == name,
                    Artifact.sha256 == digest,
                    Artifact.source == source,
                )
                .limit(1)
            )
        if existing is not None:
            return existing
        return await self.create(
            chat_id=chat_id, name=name, content_type=content_type, data=data, source=source
        )

    async def get(self, artifact_id: UUID, owner: UUID) -> Artifact:
        """Return the metadata of an artifact of ``owner`` or raise `NotFoundError`."""
        async with self._sessions() as session:
            artifact = await session.get(Artifact, artifact_id)
        if artifact is None or artifact.created_by != owner:
            raise NotFoundError(f"artifact {artifact_id} not found")
        return artifact

    async def read(self, artifact_id: UUID, owner: UUID) -> tuple[Artifact, bytes]:
        """Return the metadata and content of an artifact of ``owner`` or raise `NotFoundError`."""
        artifact = await self.get(artifact_id, owner)
        return artifact, await self._blobs.get(artifact_key(artifact_id))
