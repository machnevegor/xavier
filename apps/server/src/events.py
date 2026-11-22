"""Chat event log and PostgreSQL ``LISTEN``/``NOTIFY``."""

import asyncio
import contextlib
import logging
from collections.abc import AsyncGenerator, Sequence
from dataclasses import dataclass
from typing import Self
from uuid import UUID

import asyncpg
from pydantic import JsonValue
from sqlalchemy import func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.engine import Sessions
from src.db.models import Event

EVENTS_CHANNEL = "xv_events"
"""Payload ``<chat_id>``: new events of the chat were committed."""
ACTIONS_CHANNEL = "xv_action"
"""Payload ``<action_id>``: the action status changed."""
MESSAGES_CHANNEL = "xv_message"
"""Payload ``<chat_id>``: operator input was queued; a running turn of the chat yields to it."""
QUESTIONS_CHANNEL = "xv_question"
"""Payload ``<question_id>``: the question was answered."""

_BATCH_INTERVAL = 0.25
_BATCH_SIZE = 50
_RECONNECT_DELAY = 1.0

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class NewEvent:
    """An event to append."""

    kind: str
    data: JsonValue
    turn_id: UUID | None = None


async def append_events(session: AsyncSession, chat_id: UUID, events: Sequence[NewEvent]) -> None:
    """Append ``events`` to the chat feed and notify listeners on commit.

    A per-chat transaction lock makes event ids grow in commit order, so readers after a cursor
    never miss an event. Commit promptly: the lock is held until then.
    """
    if not events:
        return
    await session.execute(
        select(func.pg_advisory_xact_lock(func.hashtextextended(f"{EVENTS_CHANNEL}:{chat_id}", 0)))
    )
    await session.execute(
        insert(Event),
        [
            {"chat_id": chat_id, "turn_id": e.turn_id, "kind": e.kind, "data": e.data}
            for e in events
        ],
    )
    await notify(session, EVENTS_CHANNEL, str(chat_id))


async def notify(session: AsyncSession, channel: str, payload: str) -> None:
    """Send ``NOTIFY`` when the session's transaction commits."""
    await session.execute(select(func.pg_notify(channel, payload)))


async def read_events(
    session: AsyncSession, chat_id: UUID, after: UUID | None, limit: int
) -> Sequence[Event]:
    """Return up to ``limit`` events of the chat after the cursor ``after``, oldest first."""
    query = select(Event).where(Event.chat_id == chat_id)
    if after is not None:
        query = query.where(Event.id > after)
    return (await session.scalars(query.order_by(Event.id).limit(limit))).all()


class EventBatcher:
    """Buffer events of one chat and write them every 250 ms or every 50 events."""

    def __init__(self, sessions: Sessions, chat_id: UUID) -> None:
        self._sessions = sessions
        self._chat_id = chat_id
        self._buffer: list[NewEvent] = []
        self._full = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    def add(self, event: NewEvent) -> None:
        """Queue ``event``."""
        self._buffer.append(event)
        if len(self._buffer) >= _BATCH_SIZE:
            self._full.set()

    async def __aenter__(self) -> Self:
        """Start periodic flushing."""
        self._task = asyncio.create_task(self._run())
        return self

    async def __aexit__(self, *_: object) -> None:
        """Stop periodic flushing and write the remaining events."""
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        await self.flush()

    async def flush(self) -> None:
        """Write the buffered events now."""
        batch, self._buffer = self._buffer, []
        self._full.clear()
        if batch:
            async with self._sessions.begin() as session:
                await append_events(session, self._chat_id, batch)

    async def _run(self) -> None:
        while True:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._full.wait(), _BATCH_INTERVAL)
            await self.flush()


class Listener:
    """One ``LISTEN`` connection shared by all waiters of a channel in the process.

    After a reconnect every subscriber receives ``None``: notifications may have been lost, and
    the subscriber re-reads its state.
    """

    def __init__(self, dsn: str, channel: str) -> None:
        self._dsn = dsn
        self._channel = channel
        self._queues: set[asyncio.Queue[str | None]] = set()
        self._task: asyncio.Task[None] | None = None
        self._ready = asyncio.Event()

    async def start(self) -> None:
        """Connect and keep the connection open in the background."""
        self._task = asyncio.create_task(self._run())
        await self._ready.wait()

    async def close(self) -> None:
        """Stop listening."""
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    @contextlib.asynccontextmanager
    async def subscribe(self) -> AsyncGenerator[asyncio.Queue[str | None]]:
        """Yield a queue of payloads received while the context is open."""
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        self._queues.add(queue)
        try:
            yield queue
        finally:
            self._queues.discard(queue)

    def _deliver(self, _connection: object, _pid: int, _channel: str, payload: str) -> None:
        for queue in self._queues:
            queue.put_nowait(payload)

    async def _run(self) -> None:
        first = True
        while True:
            try:
                connection = await asyncpg.connect(self._dsn)
            except OSError, asyncpg.PostgresError:
                logger.warning("cannot connect to listen on %s; retrying", self._channel)
                await asyncio.sleep(_RECONNECT_DELAY)
                continue
            closed = asyncio.Event()
            connection.add_termination_listener(lambda _connection, closed=closed: closed.set())
            try:
                await connection.add_listener(self._channel, self._deliver)
                if not first:
                    for queue in self._queues:
                        queue.put_nowait(None)
                first = False
                self._ready.set()
                await closed.wait()
                logger.warning("listen connection on %s lost; reconnecting", self._channel)
            finally:
                if not connection.is_closed():
                    await connection.close()
            await asyncio.sleep(_RECONNECT_DELAY)
