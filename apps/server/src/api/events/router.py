"""The chat feed: pages, a server-sent event stream and images of agent answers.

The feed is the ordered log of what happens in a chat; event ids grow in commit order. A
client reads a page, then follows the stream from the last event id. Deep links resolve to a
position in the feed, which is read with `cursor` and `page`.
"""

from typing import Annotated
from uuid import UUID

from fastapi import Header, Query, Response
from sqlalchemy import select
from sse_starlette import EventSourceResponse

from src.agent import embeds
from src.api.common import paginate, router
from src.api.dependencies import OwnedChatDep, ServicesDep, SessionDep, StreamUserDep
from src.api.events.schemas import Event, EventsSort, ListEventsInput, ListEventsResult
from src.api.events.stream import chat_events
from src.db import models as db
from src.errors import NotFoundError, UnavailableError

router = router("/chats/{chat_id}/events", "events")

_SORT = {EventsSort.ID: db.Event.id, EventsSort.CREATED_AT: db.Event.created_at}
_PING_S = 15


@router.get("")
async def list_events(
    chat_id: UUID,
    params: Annotated[ListEventsInput, Query()],
    _: OwnedChatDep,
    session: SessionDep,
) -> ListEventsResult:
    """List the chat feed, oldest first by default; `cursor` continues after an event."""
    query = select(db.Event).where(db.Event.chat_id == chat_id)
    if params.turn_id:
        query = query.where(db.Event.turn_id.in_(params.turn_id))
    rows, info = await paginate(
        session, query, params, sort=_SORT[params.sort], direction=params.direction, key=db.Event.id
    )
    return ListEventsResult(data=[Event.model_validate(row) for row in rows], info=info)


@router.get("/stream", response_class=EventSourceResponse)
async def stream_events(
    chat_id: UUID,
    principal: StreamUserDep,
    services: ServicesDep,
    cursor: Annotated[
        UUID | None, Query(description="_Optional_. ID of the event to resume after")
    ] = None,
    last_event_id: Annotated[
        UUID | None,
        Header(description="_Optional_. ID of the last received event; takes precedence"),
    ] = None,
) -> EventSourceResponse:
    """Stream the chat feed as server-sent events.

    Each event's `data` is an event object and its SSE id is the event id. Reconnecting with
    `Last-Event-ID` (or `cursor`) resumes after that event. The stream ends when the session or
    API key is revoked or the chat is deleted.
    """
    async with services.sessions() as session:
        found = await session.scalar(
            select(db.Chat.id).where(
                db.Chat.id == chat_id,
                db.Chat.created_by == principal.user_id,
                db.Chat.deleted_at.is_(None),
            )
        )
    if found is None:
        raise NotFoundError(f"chat {chat_id} not found")
    return EventSourceResponse(
        chat_events(services, principal, chat_id, last_event_id or cursor), ping=_PING_S
    )


@router.get(
    "/{event_id}/media/{index}",
    response_class=Response,
    responses={200: {"content": {"image/*": {}}, "description": "The image"}},
)
async def read_event_media(
    chat_id: UUID,
    event_id: UUID,
    index: int,
    _: OwnedChatDep,
    session: SessionDep,
    services: ServicesDep,
) -> Response:
    """Return image `index` of the HTTPS images an agent answer shows.

    Only URLs stored in that answer are served: fetched once from a public address and kept.
    PNG, JPEG, GIF and WebP up to 10 MiB; 503 when the image cannot be fetched or is not one.
    """
    event = await session.get(db.Event, event_id)
    data = event.data if event is not None and event.chat_id == chat_id else None
    urls = data.get("media") if isinstance(data, dict) else None
    url = urls[index] if isinstance(urls, list) and 0 <= index < len(urls) else None
    if not isinstance(url, str):
        raise NotFoundError(f"media {index} of event {event_id} not found")
    await session.commit()
    try:
        body, kind = await embeds.media(url, services.blobs, services.fetcher)
    except embeds.MediaError as error:
        raise UnavailableError(str(error)) from None
    return Response(
        body,
        media_type=kind,
        headers={
            "Cache-Control": "private, max-age=86400, immutable",
            "X-Content-Type-Options": "nosniff",
        },
    )
