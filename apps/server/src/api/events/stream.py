"""Server-sent chat events."""

import asyncio
import contextlib
import time
from collections.abc import AsyncGenerator
from uuid import UUID

from sqlalchemy import select
from sse_starlette import ServerSentEvent

from src.api.dependencies import STREAM_CHECK_S, Services, still_active
from src.api.events.schemas import Event
from src.auth.service import Principal
from src.db import models as db
from src.events import read_events

_BATCH = 200


async def chat_events(
    services: Services, principal: Principal, chat_id: UUID, after: UUID | None
) -> AsyncGenerator[ServerSentEvent]:
    """Yield the chat events after ``after``, then new ones as they are committed.

    The event id is the SSE id, so a reconnecting client resumes from ``Last-Event-ID``. Every
    `STREAM_CHECK_S` seconds the principal and the chat are checked again; the stream ends once
    the principal is signed out or the chat is deleted. Each read opens a short session of its
    own: the stream outlives the request.
    """
    async with services.events.subscribe() as notifications:
        checked = time.monotonic()
        while True:
            if time.monotonic() - checked >= STREAM_CHECK_S:
                if not await _allowed(services, principal, chat_id):
                    return
                checked = time.monotonic()
            async with services.sessions() as session:
                events = await read_events(session, chat_id, after, _BATCH)
            for event in events:
                yield ServerSentEvent(
                    data=Event.model_validate(event).model_dump_json(), id=str(event.id)
                )
                after = event.id
            if len(events) == _BATCH:
                continue
            remaining = checked + STREAM_CHECK_S - time.monotonic()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(_changed(notifications, chat_id), max(remaining, 0))


async def _changed(notifications: asyncio.Queue[str | None], chat_id: UUID) -> None:
    """Return once the chat has new events or notifications may have been lost."""
    while await notifications.get() not in {None, str(chat_id)}:
        pass


async def _allowed(services: Services, principal: Principal, chat_id: UUID) -> bool:
    if not await still_active(services, principal):
        return False
    async with services.sessions() as session:
        chat = await session.scalar(
            select(db.Chat.id).where(
                db.Chat.id == chat_id,
                db.Chat.created_by == principal.user_id,
                db.Chat.deleted_at.is_(None),
            )
        )
    return chat is not None
