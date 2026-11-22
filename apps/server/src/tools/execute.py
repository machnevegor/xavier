"""Execute tools."""

from collections.abc import Sequence
from typing import Literal, Self
from uuid import UUID

from pydantic import Field, JsonValue, model_validator

from src.db.models import FINAL_EXECUTION_STATUSES, Execution, ExecutionKind, ToolGroup
from src.errors import NotFoundError
from src.execute.service import ExecutionRequest
from src.policy.model import Resource, ResourceKind, Risk
from src.policy.urls import workspace_path
from src.tools.base import Tool, ToolArgs, ToolContext, ToolError, ToolResult, use_workspace
from src.workspace.service import WorkspaceError

MAX_WAIT_SECONDS = 110
MAX_CODE_CHARS = 200_000
MAX_EXPORTS = 20


class CodeRunArgs(ToolArgs):
    """Arguments of `code_run`."""

    language: Literal["python", "node", "bash"] = Field(
        default="python", description="Interpreter of `code`."
    )
    code: str | None = Field(
        default=None, max_length=MAX_CODE_CHARS, description="Program text to run."
    )
    command: str | None = Field(
        default=None, max_length=10_000, description="A bash command line, instead of `code`."
    )
    network: bool = Field(
        default=False,
        description="Give the run internet access; refused unless the chat enables it.",
    )
    export: list[str] = Field(
        default=[],
        max_length=MAX_EXPORTS,
        description="Paths under /workspace that become artifacts after the run.",
    )
    wait_s: int = Field(
        default=60,
        ge=0,
        le=MAX_WAIT_SECONDS,
        description="Seconds to wait for the result; a longer run returns a `job_id`.",
    )

    @model_validator(mode="after")
    def _one_program(self) -> Self:
        if (self.code is None) == (self.command is None):
            raise ValueError("pass exactly one of `code` and `command`")
        return self


def execution_result(execution: Execution) -> dict[str, JsonValue]:
    """Return what the model sees of an execution."""
    if execution.status not in FINAL_EXECUTION_STATUSES:
        return {
            "job_id": str(execution.id),
            "job_status": execution.status.value,
            "message": "still running; call `job` to wait for it or cancel it",
        }
    result: dict[str, JsonValue] = {
        "job_id": str(execution.id),
        "job_status": execution.status.value,
        "exit_code": execution.exit_code,
        "stdout": execution.stdout,
        "truncated": execution.truncated,
        "error": execution.error,
    }
    if execution.kind is ExecutionKind.CODE:
        result |= {
            "stderr": execution.stderr,
            "artifact_ids": [str(artifact_id) for artifact_id in execution.output_artifact_ids],
            "metrics": execution.metrics,
        }
    return result


async def wait_for_jobs(
    ctx: ToolContext, job_ids: Sequence[UUID], within: float, *, first: bool = False
) -> list[dict[str, JsonValue]]:
    """Wait for jobs of the chat and return what the model sees of each.

    The wait ends early when new operator input interrupts the turn. Final results are recorded
    as received, so their completion is not reported to the chat again.

    Raises
    ------
    ToolError
        ``not_found`` if a job is not one of this chat.
    """
    executions = ctx.services.executions
    jobs = [await _job(ctx, job_id) for job_id in job_ids]
    running = [job.id for job in jobs if job.status not in FINAL_EXECUTION_STATUSES]
    if running and within > 0:
        await ctx.wait_or_interrupt(executions.until_done(running, within, first=first), within)
        jobs = [await executions.get(job_id) for job_id in job_ids]
    await executions.collect([job.id for job in jobs if job.status in FINAL_EXECUTION_STATUSES])
    return [execution_result(job) for job in jobs]


async def _job(ctx: ToolContext, job_id: UUID) -> Execution:
    try:
        job = await ctx.services.executions.get(job_id)
    except NotFoundError:
        job = None
    if job is None or job.chat_id != ctx.chat_id:
        raise ToolError("not_found", f"no job {job_id} in this chat")
    return job


class CodeRun(Tool[CodeRunArgs]):
    """Run code in an isolated container with the chat's /workspace."""

    name = "code_run"
    groups = frozenset({ToolGroup.EXECUTE})
    description = (
        "Run Python, Node or bash code in a fresh isolated container. The chat's /workspace "
        "is mounted and persists between runs and sessions; everything else is discarded. "
        "Network is off unless `network` is true and the chat allows it. Files listed in "
        "`export` become artifacts the user can download. Use it for computation, data "
        "processing and short scripts; use the Computer for browsing and applications."
    )
    consequences = (
        "Runs the code in a sandbox that can read and change files in the chat /workspace; "
        "with network it can reach the internet."
    )
    risk = Risk.WRITE
    args_type = CodeRunArgs

    def prepare(self, args: CodeRunArgs) -> CodeRunArgs:
        """Normalize the export paths."""
        return args.model_copy(update={"export": [workspace_path(path) for path in args.export]})

    def resources(self, args: CodeRunArgs) -> list[Resource]:
        """Return the exported paths."""
        return [Resource(ResourceKind.PATH, path) for path in args.export]

    async def run(self, args: CodeRunArgs, ctx: ToolContext) -> ToolResult:
        """Start the run and wait up to ``wait_s`` for it."""
        if args.network and not ctx.execute_network:
            raise ToolError("denied", "network access is disabled for code runs in this chat")
        active, extra = await use_workspace(ctx)
        try:
            await ctx.services.workspaces.ensure_room(active)
        except WorkspaceError as error:
            raise ToolError(error.code, error.message) from error
        executions = ctx.services.executions
        execution_id = await executions.start(
            ExecutionRequest(
                language=args.language,
                code=args.code,
                command=args.command,
                network=args.network,
                export=args.export,
            ),
            task_queue=ctx.services.host_queue,
            chat_id=ctx.chat_id,
            action_id=ctx.action_id,
        )
        [result] = await wait_for_jobs(ctx, [execution_id], args.wait_s)
        return ToolResult({**result, **extra})
