"""Stored part of the workspace status report, readable without the worker."""

from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import select

from src.config import Settings
from src.db.engine import Sessions
from src.db.models import Event, Workspace

FAILURE_STATUSES = ("lost", "full", "computer_failed")
"""Statuses of ``workspace`` feed events that report a failure."""
RECENT_FAILURES = 5


def lifecycle(row: Workspace | None, settings: Settings) -> dict[str, JsonValue]:
    """Return the report of a workspace as stored, without Computer health and failures."""
    if row is None:
        return {"workspace": {"status": "none"}, "computer": None}
    return {
        "workspace": {
            "status": row.status.value,
            "host": row.host,
            "checkpoint_at": row.checkpoint_at.isoformat() if row.checkpoint_at else None,
            "touched_at": row.touched_at.isoformat(),
            "size_bytes": row.size_bytes,
            "size_limit_bytes": int(settings.workspace.size_limit),
        },
        "computer": None,
    }


async def recent_failures(sessions: Sessions, chat_id: UUID) -> list[JsonValue]:
    """Return the latest failures of the workspace and its Computer, newest first."""
    async with sessions() as session:
        rows = await session.scalars(
            select(Event)
            .where(
                Event.chat_id == chat_id,
                Event.kind == "workspace",
                Event.data["status"].astext.in_(FAILURE_STATUSES),
            )
            .order_by(Event.id.desc())
            .limit(RECENT_FAILURES)
        )
        return [
            {**row.data, "at": row.created_at.isoformat()}
            for row in rows
            if isinstance(row.data, dict)
        ]
