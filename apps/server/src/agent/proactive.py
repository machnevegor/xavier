"""Proactive mode: turns the agent starts itself and their limits."""

import uuid
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import ColumnElement, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import ProactiveSettings
from src.db.models import Chat, Turn, TurnTrigger, Wakeup, WakeupStatus
from src.events import NewEvent, append_events
from src.orchestration.models import Admission

DAY = timedelta(days=1)


class WakeupLimitError(Exception):
    """The wakeup is outside the Proactive limits."""


async def admission(session: AsyncSession, chat_id: UUID, limits: ProactiveSettings) -> Admission:
    """Return whether a turn the agent starts itself fits the limits now, or when it will."""
    chat = await session.get(Chat, chat_id)
    if chat is None or not chat.proactive:
        return Admission(allowed=False, reason="proactive mode is off")
    now = datetime.now(UTC)
    starts = (
        await session.scalars(
            select(Turn.started_at)
            .where(
                Turn.chat_id == chat_id,
                Turn.trigger != TurnTrigger.INPUT,
                Turn.started_at > now - DAY,
            )
            .order_by(Turn.started_at)
        )
    ).all()
    if len(starts) >= limits.max_wakeups_per_day:
        return Admission(
            allowed=False,
            retry_at=starts[len(starts) - limits.max_wakeups_per_day] + DAY,
            reason="daily limit of proactive turns",
        )
    if starts and starts[-1] + limits.min_interval > now:
        return Admission(
            allowed=False,
            retry_at=starts[-1] + limits.min_interval,
            reason="minimum interval between proactive turns",
        )
    if chat.spent_usd >= chat.budget_usd:
        return Admission(allowed=False, reason="chat budget is spent")
    return Admission(allowed=True)


async def schedule(  # noqa: PLR0913 - the parts of a wakeup
    session: AsyncSession,
    chat_id: UUID,
    *,
    due_at: datetime,
    reason: str,
    action_id: UUID | None,
    limits: ProactiveSettings,
) -> Wakeup:
    """Store a pending wakeup.

    Raises
    ------
    WakeupLimitError
        If the chat has too many pending wakeups or the time is out of range.
    """
    now = datetime.now(UTC)
    if due_at <= now or due_at > now + limits.max_delay:
        raise WakeupLimitError(f"the time must be in the future, within {limits.max_delay}")
    pending = await session.scalar(
        select(func.count())
        .select_from(Wakeup)
        .where(Wakeup.chat_id == chat_id, Wakeup.status == WakeupStatus.PENDING)
    )
    if (pending or 0) >= limits.max_pending_wakeups:
        raise WakeupLimitError(f"at most {limits.max_pending_wakeups} pending wakeups")
    wakeup = Wakeup(
        id=uuid.uuid7(),
        chat_id=chat_id,
        due_at=due_at,
        reason=reason,
        status=WakeupStatus.PENDING,
        action_id=action_id,
    )
    session.add(wakeup)
    await session.flush()
    await append_events(session, chat_id, [wakeup_event(wakeup)])
    return wakeup


async def cancel(
    session: AsyncSession, chat_id: UUID, *conditions: ColumnElement[bool]
) -> list[Wakeup]:
    """Cancel the pending wakeups of the chat that match ``conditions``; return them."""
    wakeups = (
        await session.scalars(
            update(Wakeup)
            .where(Wakeup.chat_id == chat_id, Wakeup.status == WakeupStatus.PENDING, *conditions)
            .values(status=WakeupStatus.CANCELLED)
            .returning(Wakeup)
        )
    ).all()
    await append_events(session, chat_id, [wakeup_event(wakeup) for wakeup in wakeups])
    return list(wakeups)


def wakeup_event(wakeup: Wakeup) -> NewEvent:
    """Return the feed event of a wakeup's state."""
    return NewEvent(
        "wakeup",
        {
            "wakeup_id": str(wakeup.id),
            "status": wakeup.status.value,
            "reason": wakeup.reason,
            "due_at": wakeup.due_at.isoformat(),
        },
    )
