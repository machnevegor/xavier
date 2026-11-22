"""The images of a chat as one gallery.

The gallery holds the chat's image files (uploads, screenshots, exports, results and files
that answers reference; PNG, JPEG, GIF and WebP), each content once, and the web images that
agent answers show, each address once. Web images are served through
`GET /chats/{chat_id}/events/{event_id}/media/{index}`.
"""

from collections.abc import Sequence
from typing import Annotated
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import Query
from sqlalchemy import select

from src.api.common import SortDirection, paginate_items, router
from src.api.dependencies import OwnedChatDep, SessionDep
from src.api.media.schemas import ListMediaInput, ListMediaResult, Media, MediaKind
from src.db import models as db

router = router("/chats/{chat_id}/media", "media")

IMAGE_TYPES = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp"})


@router.get("")
async def list_media(
    chat_id: UUID,
    _: OwnedChatDep,
    session: SessionDep,
    params: Annotated[ListMediaInput, Query()],
) -> ListMediaResult:
    """List the chat images, newest first by default."""
    kinds = set(params.kind or MediaKind)
    artifacts: Sequence[db.Artifact] = []
    events: Sequence[db.Event] = []
    if MediaKind.ARTIFACT in kinds:
        artifacts = (
            await session.scalars(
                select(db.Artifact)
                .where(db.Artifact.chat_id == chat_id, db.Artifact.content_type.in_(IMAGE_TYPES))
                .order_by(db.Artifact.id.desc())
            )
        ).all()
    if MediaKind.EXTERNAL in kinds:
        events = (
            await session.scalars(
                select(db.Event)
                .where(
                    db.Event.chat_id == chat_id,
                    db.Event.kind == "assistant",
                    db.Event.data.op("?")("media"),
                )
                .order_by(db.Event.id.desc())
            )
        ).all()
    items = _gallery(chat_id, artifacts, events)
    items.sort(key=lambda item: item.created_at, reverse=params.direction is SortDirection.DESC)
    page, info = paginate_items(items, params)
    return ListMediaResult(data=page, info=info)


def _gallery(
    chat_id: UUID, artifacts: Sequence[db.Artifact], events: Sequence[db.Event]
) -> list[Media]:
    """Return the images, each content and each web address once, the newest copy kept."""
    items: list[Media] = []
    digests: set[str] = set()
    for artifact in artifacts:
        if artifact.sha256 in digests:
            continue
        digests.add(artifact.sha256)
        items.append(
            Media(
                id=str(artifact.id),
                kind=MediaKind.ARTIFACT,
                name=artifact.name,
                content_type=artifact.content_type,
                size_bytes=artifact.size_bytes,
                url=f"/artifacts/{artifact.id}/content",
                source_url=None,
                artifact_id=artifact.id,
                event_id=None,
                created_at=artifact.created_at,
            )
        )
    urls: set[str] = set()
    for event in events:
        media = event.data.get("media") if isinstance(event.data, dict) else None
        if not isinstance(media, list):
            continue
        for index, url in enumerate(media):
            if not isinstance(url, str) or url in urls:
                continue
            urls.add(url)
            parts = urlsplit(url)
            items.append(
                Media(
                    id=f"{event.id}:{index}",
                    kind=MediaKind.EXTERNAL,
                    name=parts.path.rsplit("/", 1)[-1] or parts.hostname or url,
                    content_type=None,
                    size_bytes=None,
                    url=f"/chats/{chat_id}/events/{event.id}/media/{index}",
                    source_url=url,
                    artifact_id=None,
                    event_id=event.id,
                    created_at=event.created_at,
                )
            )
    return items
