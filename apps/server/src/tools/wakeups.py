"""Wakeups the agent schedules for itself in Proactive mode."""

from datetime import UTC, datetime, timedelta
from typing import Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, Field, JsonValue, model_validator
from sqlalchemy import select

from src.agent import proactive
from src.db.models import Chat, ToolGroup, Wakeup, WakeupStatus
from src.policy.model import Risk
from src.tools.base import Tool, ToolArgs, ToolContext, ToolError, ToolResult


class WakeupArgs(ToolArgs):
    """Arguments of `wakeup`."""

    action: Literal["schedule", "list", "cancel"] = Field(
        description="`schedule` a wakeup, `list` the pending ones or `cancel` one."
    )
    at: AwareDatetime | None = Field(
        default=None, description="When to wake up, with a time zone; or use `after_s`."
    )
    after_s: int | None = Field(default=None, ge=1, description="Seconds from now to wake up.")
    reason: str = Field(
        default="",
        max_length=500,
        description="What to do then; it comes back in the `wakeup` notice.",
    )
    wakeup_id: UUID | None = Field(default=None, description="Wakeup to `cancel`.")

    @model_validator(mode="after")
    def _complete(self) -> Self:
        if self.action == "schedule" and ((self.at is None) == (self.after_s is None)):
            raise ValueError("`schedule` takes exactly one of `at` and `after_s`")
        if self.action == "schedule" and not self.reason.strip():
            raise ValueError("`schedule` needs a `reason`")
        if self.action == "cancel" and self.wakeup_id is None:
            raise ValueError("`cancel` needs `wakeup_id`")
        return self


class WakeupTool(Tool[WakeupArgs]):
    """Schedule, list and cancel the agent's own wakeups."""

    name = "wakeup"
    groups = frozenset({ToolGroup.PROACTIVE})
    description = (
        "Schedule a later turn for yourself in this chat: at the time you name, you get a "
        "`wakeup` notice with your reason and can continue the work. A wakeup never interrupts "
        "a turn; it waits for the current one to end. Daily and interval limits may postpone it, "
        "and it is cancelled if the user turns Proactive mode off."
    )
    consequences = "The agent starts a turn by itself at the scheduled time."
    risk = Risk.READ
    interrupt_safe = True
    args_type = WakeupArgs

    async def run(self, args: WakeupArgs, ctx: ToolContext) -> ToolResult:
        """Schedule, list or cancel."""
        match args.action:
            case "schedule":
                return ToolResult({"wakeup": _view(await _schedule(args, ctx))})
            case "cancel":
                async with ctx.services.sessions.begin() as session:
                    cancelled = await proactive.cancel(
                        session, ctx.chat_id, Wakeup.id == args.wakeup_id
                    )
                if not cancelled:
                    raise ToolError("not_found", f"no pending wakeup {args.wakeup_id}")
                return ToolResult({"wakeup": _view(cancelled[0])})
            case _:
                async with ctx.services.sessions() as session:
                    pending = await session.scalars(
                        select(Wakeup)
                        .where(
                            Wakeup.chat_id == ctx.chat_id,
                            Wakeup.status == WakeupStatus.PENDING,
                        )
                        .order_by(Wakeup.due_at)
                    )
                    return ToolResult({"wakeups": [_view(wakeup) for wakeup in pending]})


async def _schedule(args: WakeupArgs, ctx: ToolContext) -> Wakeup:
    due_at = args.at or datetime.now(UTC) + timedelta(seconds=args.after_s or 0)
    async with ctx.services.sessions.begin() as session:
        chat = await session.get_one(Chat, ctx.chat_id)
        if not chat.proactive:
            raise ToolError("needs_capability", "Proactive mode is off in this chat")
        try:
            return await proactive.schedule(
                session,
                ctx.chat_id,
                due_at=due_at,
                reason=args.reason.strip(),
                action_id=ctx.action_id,
                limits=ctx.services.settings.proactive,
            )
        except proactive.WakeupLimitError as error:
            raise ToolError("invalid_args", str(error)) from error


def _view(wakeup: Wakeup) -> JsonValue:
    return {
        "wakeup_id": str(wakeup.id),
        "due_at": wakeup.due_at.isoformat(),
        "reason": wakeup.reason,
        "status": wakeup.status.value,
    }
