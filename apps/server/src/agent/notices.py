"""Platform notices passed to the model as ``[xavier:<kind>] {json}`` text blocks."""

import json
from collections.abc import Sequence
from enum import StrEnum
from uuid import UUID

from deepseek_harness import JsonObject
from pydantic import BaseModel, JsonValue
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Execution


class NoticeKind(StrEnum):
    """Kinds of platform notices."""

    APPROVAL_RESULT = "approval_result"
    RECOVERY = "recovery"
    WORKSPACE_RESTORED = "workspace_restored"
    RESUMED = "resumed"
    LIMIT_REACHED = "limit_reached"
    LIMITS_CHANGED = "limits_changed"
    CAPABILITIES = "capabilities"
    INTERRUPTED = "interrupted"
    HISTORY_REWOUND = "history_rewound"
    JOB_FINISHED = "job_finished"
    WAKEUP = "wakeup"
    ALERT = "alert"
    WORKSPACE_FAILED = "workspace_failed"


class Notice(BaseModel):
    """One platform notice."""

    kind: NoticeKind
    data: dict[str, JsonValue] = {}


def render(notice: Notice) -> JsonObject:
    """Return the notice as a text content block."""
    payload = json.dumps(notice.data, ensure_ascii=False, separators=(",", ":"))
    return {"type": "text", "text": f"[xavier:{notice.kind}] {payload}"}


async def current(session: AsyncSession, notices: Sequence[Notice]) -> list[Notice]:
    """Drop notices of finished jobs whose results the agent read after they were reported."""
    jobs = {_job(notice) for notice in notices if notice.kind is NoticeKind.JOB_FINISHED}
    if not jobs:
        return list(notices)
    collected = set(
        await session.scalars(
            select(Execution.id).where(Execution.id.in_(jobs), Execution.collected_at.is_not(None))
        )
    )
    return [
        notice
        for notice in notices
        if notice.kind is not NoticeKind.JOB_FINISHED or _job(notice) not in collected
    ]


def _job(notice: Notice) -> UUID:
    return UUID(str(notice.data["job_id"]))
