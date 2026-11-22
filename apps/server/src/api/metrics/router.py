"""Metrics summarize the usage of the user's chats and code runs over a period.

The period `[start, end)` spans at most 366 days, and the daily series uses UTC days. Turns and
runs count by the time they started; chats and runs of deleted chats are left out.
"""

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import Query
from sqlalchemy import extract, func, or_, select

from src.api.common import router
from src.api.dependencies import CurrentUserDep, SessionDep
from src.api.metrics.schemas import (
    MetricsSummary,
    ReadMetricsSummaryInput,
    ReadMetricsSummaryResult,
    UsageDay,
)
from src.db import models as db
from src.errors import InvalidRequestError

router = router("/metrics", "metrics")

DEFAULT_PERIOD = timedelta(days=30)
MAX_PERIOD = timedelta(days=366)


@router.get("/summary")
async def read_metrics_summary(
    principal: CurrentUserDep,
    session: SessionDep,
    params: Annotated[ReadMetricsSummaryInput, Query()],
) -> ReadMetricsSummaryResult:
    """Summarize turns, tokens, cost and code runs started in the period, with a daily series."""
    end = params.end or datetime.now(UTC)
    start = params.start or end - DEFAULT_PERIOD
    if not start < end <= start + MAX_PERIOD:
        raise InvalidRequestError("`start` must precede `end` by at most 366 days")
    owner = principal.user_id
    chat_in = (db.Chat.created_by == owner) & db.Chat.deleted_at.is_(None)
    turn_in = chat_in & (db.Turn.started_at >= start) & (db.Turn.started_at < end)
    execution_in = (
        (db.Execution.created_by == owner)
        & or_(db.Execution.chat_id.is_(None), db.Chat.deleted_at.is_(None))
        & (db.Execution.created_at >= start)
        & (db.Execution.created_at < end)
    )
    day = func.date_trunc("day", func.timezone("UTC", db.Turn.started_at)).label("day")
    chats = await session.scalar(
        select(func.count()).where(chat_in, db.Chat.created_at >= start, db.Chat.created_at < end)
    )
    turns = dict(
        (
            await session.execute(
                select(db.Turn.status, func.count())
                .join(db.Chat, db.Chat.id == db.Turn.chat_id)
                .where(turn_in)
                .group_by(db.Turn.status)
            )
        ).all()
    )
    turn_seconds, input_tokens, output_tokens, cache_read_tokens, cost_usd = (
        await session.execute(
            select(
                func.coalesce(
                    func.sum(extract("epoch", db.Turn.finished_at - db.Turn.started_at)), 0.0
                ),
                func.coalesce(func.sum(db.Turn.input_tokens), 0),
                func.coalesce(func.sum(db.Turn.output_tokens), 0),
                func.coalesce(func.sum(db.Turn.cache_read_tokens), 0),
                func.coalesce(func.sum(db.Turn.cost_usd), 0.0),
            )
            .select_from(db.Turn)
            .join(db.Chat, db.Chat.id == db.Turn.chat_id)
            .where(turn_in)
        )
    ).one()
    executions = dict(
        (
            await session.execute(
                select(db.Execution.status, func.count())
                .outerjoin(db.Chat, db.Chat.id == db.Execution.chat_id)
                .where(execution_in)
                .group_by(db.Execution.status)
            )
        ).all()
    )
    execution_seconds = await session.scalar(
        select(
            func.coalesce(
                func.sum(extract("epoch", db.Execution.finished_at - db.Execution.started_at)),
                0.0,
            )
        )
        .select_from(db.Execution)
        .outerjoin(db.Chat, db.Chat.id == db.Execution.chat_id)
        .where(execution_in)
    )
    days = await session.execute(
        select(
            day,
            func.count(),
            func.sum(db.Turn.input_tokens),
            func.sum(db.Turn.output_tokens),
            func.sum(db.Turn.cache_read_tokens),
            func.sum(db.Turn.cost_usd),
        )
        .select_from(db.Turn)
        .join(db.Chat, db.Chat.id == db.Turn.chat_id)
        .where(turn_in)
        .group_by(day)
        .order_by(day)
    )
    return ReadMetricsSummaryResult(
        data=MetricsSummary(
            start=start,
            end=end,
            chats=chats or 0,
            turns={status: turns.get(status, 0) for status in db.TurnStatus},
            turn_seconds=float(turn_seconds),
            input_tokens=int(input_tokens),
            output_tokens=int(output_tokens),
            cache_read_tokens=int(cache_read_tokens),
            cost_usd=float(cost_usd),
            executions={status: executions.get(status, 0) for status in db.ExecutionStatus},
            execution_seconds=float(execution_seconds or 0.0),
            days=[
                UsageDay(
                    day=row_day.date(),
                    turns=count,
                    input_tokens=int(inputs),
                    output_tokens=int(outputs),
                    cache_read_tokens=int(cached),
                    cost_usd=float(cost),
                )
                for row_day, count, inputs, outputs, cached, cost in days
            ],
        )
    )
