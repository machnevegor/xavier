"""Status, waits and cancellation of long operations that returned a ``job_id``."""

from typing import Literal, Self
from uuid import UUID

from pydantic import Field, JsonValue, model_validator
from sqlalchemy import or_, select

from src.db.models import Execution, ExecutionStatus, ToolGroup
from src.policy.model import Risk
from src.tools.base import Tool, ToolArgs, ToolContext, ToolResult
from src.tools.execute import MAX_WAIT_SECONDS, wait_for_jobs

MAX_JOBS = 20
LISTED_JOBS = 50

_CANCEL_WAIT_SECONDS = 15.0
_UNFINISHED = (ExecutionStatus.PENDING, ExecutionStatus.RUNNING)


class JobArgs(ToolArgs):
    """Arguments of `job`."""

    action: Literal["status", "list", "wait", "wait_any", "wait_all", "cancel"] = Field(
        default="wait",
        description=(
            "`status` of jobs without waiting, `list` the chat's running and unread jobs, "
            "`wait` for one job, `wait_any` until one of `job_ids` finishes, `wait_all` until "
            "all of them finish, or `cancel` jobs."
        ),
    )
    job_id: UUID | None = Field(
        default=None, description="`job_id` returned by `code_run` or `terminal`."
    )
    job_ids: list[UUID] = Field(
        default=[], max_length=MAX_JOBS, description="Several jobs, instead of `job_id`."
    )
    wait_s: int = Field(
        default=60, ge=0, le=MAX_WAIT_SECONDS, description="Seconds to wait for results."
    )

    @model_validator(mode="after")
    def _jobs_named(self) -> Self:
        named = len(self.ids())
        if self.action == "list" and named:
            raise ValueError("`list` takes no jobs")
        if self.action == "wait" and named != 1:
            raise ValueError("`wait` takes exactly one job; use `wait_any` or `wait_all`")
        if self.action != "list" and not named:
            raise ValueError(f"`{self.action}` needs `job_id` or `job_ids`")
        return self

    def ids(self) -> list[UUID]:
        """Return the named jobs in order, without repeats."""
        named = [*([self.job_id] if self.job_id is not None else []), *self.job_ids]
        return list(dict.fromkeys(named))


class Job(Tool[JobArgs]):
    """Inspect, wait for or cancel the long-running jobs of this chat."""

    name = "job"
    groups = frozenset({ToolGroup.EXECUTE, ToolGroup.COMPUTE})
    description = (
        "Check, wait for or cancel jobs that `code_run` or `terminal` returned as `job_id`. "
        "Jobs run in parallel and keep running between turns until they finish or are "
        "cancelled; results report each job separately. A finished job you have not read is "
        "reported to you in a later turn. Waits end early when the user sends a message."
    )
    consequences = "Cancelling stops the jobs; work they have not saved is lost."
    risk = Risk.READ
    interrupt_safe = True
    retryable = True
    args_type = JobArgs

    async def classify(self, args: JobArgs, ctx: ToolContext) -> Risk:
        """Rate cancelling as ``write`` and the rest as ``read``."""
        del ctx
        return Risk.WRITE if args.action == "cancel" else Risk.READ

    async def run(self, args: JobArgs, ctx: ToolContext) -> ToolResult:
        """Report, wait or cancel."""
        match args.action:
            case "list":
                return ToolResult({"jobs": await _listed(ctx)})
            case "status":
                jobs = await wait_for_jobs(ctx, args.ids(), 0)
            case "cancel":
                await wait_for_jobs(ctx, args.ids(), 0)
                for job_id in args.ids():
                    await ctx.services.executions.cancel(job_id)
                jobs = await wait_for_jobs(ctx, args.ids(), _CANCEL_WAIT_SECONDS)
            case "wait_any":
                jobs = await wait_for_jobs(ctx, args.ids(), args.wait_s, first=True)
            case _:
                jobs = await wait_for_jobs(ctx, args.ids(), args.wait_s)
        if args.action == "wait":
            return ToolResult(jobs[0])
        return ToolResult({"jobs": list(jobs)})


async def _listed(ctx: ToolContext) -> list[JsonValue]:
    """Return the chat's running jobs and the finished ones the agent has not read."""
    async with ctx.services.sessions() as session:
        rows = await session.scalars(
            select(Execution)
            .where(
                Execution.chat_id == ctx.chat_id,
                or_(Execution.status.in_(_UNFINISHED), Execution.collected_at.is_(None)),
            )
            .order_by(Execution.id.desc())
            .limit(LISTED_JOBS)
        )
        return [
            {
                "job_id": str(row.id),
                "kind": row.kind.value,
                "job_status": row.status.value,
                "command": row.command if row.command is not None else row.language,
                "started_at": row.created_at.isoformat(),
            }
            for row in rows
        ]
