"""Executions: records and workflows (`Executions`), and the run itself (`ExecuteRunner`)."""

import asyncio
import contextlib
import mimetypes
import posixpath
import time
import uuid
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from temporalio.client import Client, WorkflowFailureError
from temporalio.service import RPCError, RPCStatusCode

from src.compute.channel import ComputerError
from src.config import Settings
from src.db.engine import Sessions
from src.db.models import (
    FINAL_EXECUTION_STATUSES,
    Chat,
    Execution,
    ExecutionKind,
    ExecutionStatus,
)
from src.errors import NotFoundError
from src.events import NewEvent, append_events
from src.execute.backend import Sandbox, SandboxBackend, SandboxSpec
from src.orchestration.models import ExecutionStart
from src.policy.redact import REDACTED
from src.storage.artifacts import Artifacts
from src.telemetry import EXECUTION_DURATION, traced
from src.workspace.service import MAX_FILE_BYTES, WorkspaceService
from src.workspace.volumes import WORKSPACE_MOUNT

LANGUAGES: dict[str, tuple[str, tuple[str, ...]]] = {
    "python": ("main.py", ("python3",)),
    "node": ("main.js", ("node",)),
    "bash": ("main.sh", ("bash",)),
}
"""Language, the file the code is written to and its interpreter."""
EXECUTION_WORKFLOW = "ExecutionWorkflow"
_CODE_DIR = "/tmp/xavier"  # noqa: S108
_KILLED = 137
_HEARTBEAT_SECONDS = 5.0
_JOB_CALL_TIMEOUT = _HEARTBEAT_SECONDS + 15.0

type Heartbeat = Callable[[], None]


def workflow_id(execution_id: UUID) -> str:
    """Return the id of the execution's workflow."""
    return f"execution-{execution_id}"


def execution_start(settings: Settings, execution_id: UUID, task_queue: str) -> ExecutionStart:
    """Return the input of `ExecutionWorkflow`; a run on a host queue is bound to that host."""
    return ExecutionStart(
        execution_id=execution_id,
        task_queue=task_queue,
        host_bound=task_queue != settings.temporal.task_queue,
        max_duration=settings.execute.max_duration,
    )


@dataclass(frozen=True, slots=True)
class ExecutionRequest:
    """A run to start. Exactly one of ``code`` and ``command`` is set."""

    language: str
    code: str | None
    command: str | None
    network: bool
    export: Sequence[str] = ()
    """Normalized ``/workspace`` paths that become artifacts."""
    input_artifact_ids: Sequence[UUID] = ()


class Executions:
    """Execution records and their workflows; used by the gateway, the API and activities."""

    def __init__(self, settings: Settings, sessions: Sessions, temporal: Client) -> None:
        self._settings = settings
        self._sessions = sessions
        self._temporal = temporal

    async def start(
        self,
        request: ExecutionRequest,
        *,
        task_queue: str,
        chat_id: UUID | None = None,
        action_id: UUID | None = None,
        created_by: UUID | None = None,
    ) -> UUID:
        """Record the execution and start its workflow; the run goes to ``task_queue``.

        A chat's run belongs to the chat's owner; a run outside chats needs ``created_by``.
        """
        execution_id = uuid.uuid7()
        async with self._sessions.begin() as session:
            session.add(
                Execution(
                    id=execution_id,
                    created_by=await _owner(session, chat_id, created_by),
                    chat_id=chat_id,
                    action_id=action_id,
                    status=ExecutionStatus.PENDING,
                    language=request.language,
                    code=request.code,
                    command=request.command,
                    network=request.network,
                    export=list(request.export),
                    input_artifact_ids=list(request.input_artifact_ids),
                    stdout="",
                    stderr="",
                    truncated=False,
                    output_artifact_ids=[],
                    metrics={},
                )
            )
        try:
            await self._temporal.start_workflow(
                EXECUTION_WORKFLOW,
                execution_start(self._settings, execution_id, task_queue),
                id=workflow_id(execution_id),
                task_queue=self._settings.temporal.task_queue,
            )
        except BaseException:
            await self.settle(execution_id, "the run could not be scheduled")
            raise
        return execution_id

    async def track(
        self, job_id: UUID, *, chat_id: UUID, action_id: UUID, command: str, task_queue: str
    ) -> None:
        """Record a running terminal job of the chat's Computer and follow it to its end."""
        async with self._sessions.begin() as session:
            session.add(
                Execution(
                    id=job_id,
                    created_by=await _owner(session, chat_id, None),
                    chat_id=chat_id,
                    action_id=action_id,
                    kind=ExecutionKind.TERMINAL,
                    status=ExecutionStatus.RUNNING,
                    language="bash",
                    command=command,
                    network=True,
                    export=[],
                    input_artifact_ids=[],
                    host=self._settings.worker.id,
                    started_at=func.now(),
                )
            )
        try:
            await self._temporal.start_workflow(
                EXECUTION_WORKFLOW,
                execution_start(self._settings, job_id, task_queue),
                id=workflow_id(job_id),
                task_queue=self._settings.temporal.task_queue,
            )
        except BaseException:
            await self.settle(job_id, "the job could not be followed")
            raise

    async def get(self, execution_id: UUID) -> Execution:
        """Return the execution or raise `NotFoundError`."""
        async with self._sessions() as session:
            execution = await session.get(Execution, execution_id)
        if execution is None:
            raise NotFoundError(f"execution {execution_id} not found")
        return execution

    async def wait(self, execution_id: UUID, within: float) -> Execution:
        """Wait up to ``within`` seconds for the execution to finish; return its record."""
        await self.until_done([execution_id], within)
        return await self.get(execution_id)

    async def until_done(
        self, execution_ids: Collection[UUID], within: float, *, first: bool = False
    ) -> None:
        """Wait up to ``within`` seconds for all executions to finish, or for the ``first``."""

        async def done(execution_id: UUID) -> None:
            with contextlib.suppress(WorkflowFailureError):
                await self._temporal.get_workflow_handle(workflow_id(execution_id)).result()

        waits = [asyncio.create_task(done(execution_id)) for execution_id in execution_ids]
        if not waits:
            return
        try:
            await asyncio.wait(
                waits,
                timeout=within,
                return_when=asyncio.FIRST_COMPLETED if first else asyncio.ALL_COMPLETED,
            )
        finally:
            for task in waits:
                task.cancel()
            await asyncio.gather(*waits, return_exceptions=True)

    async def collect(self, execution_ids: Collection[UUID]) -> None:
        """Record that the agent received the final results of these executions."""
        async with self._sessions.begin() as session:
            await session.execute(
                update(Execution)
                .where(
                    Execution.id.in_(list(execution_ids)),
                    Execution.status.in_(FINAL_EXECUTION_STATUSES),
                    Execution.collected_at.is_(None),
                )
                .values(collected_at=func.now())
            )

    async def cancel(self, execution_id: UUID) -> Execution:
        """Request cancellation; a run that did not start is cancelled at once."""
        execution = await self.get(execution_id)
        if execution.status in FINAL_EXECUTION_STATUSES:
            return execution
        try:
            await self._temporal.get_workflow_handle(workflow_id(execution_id)).cancel()
        except RPCError as error:
            if error.status is not RPCStatusCode.NOT_FOUND:
                raise
            await self.settle(execution_id, "cancelled", cancelled=True)
        return await self.get(execution_id)

    async def settle(self, execution_id: UUID, reason: str, *, cancelled: bool = False) -> None:
        """Close an execution whose run did not report: not started fails, started is uncertain."""
        await self.transition(
            execution_id,
            {ExecutionStatus.PENDING},
            ExecutionStatus.CANCELLED if cancelled else ExecutionStatus.FAILED,
            error=reason,
        )
        await self.transition(
            execution_id, {ExecutionStatus.RUNNING}, ExecutionStatus.UNCERTAIN, error=reason
        )

    async def transition(
        self,
        execution_id: UUID,
        expected: Collection[ExecutionStatus],
        new: ExecutionStatus,
        **values: object,
    ) -> bool:
        """Move the execution from one of ``expected`` to ``new``; return whether this call did."""
        if new in FINAL_EXECUTION_STATUSES:
            values = {"finished_at": func.now(), **values}
        async with self._sessions.begin() as session:
            row = (
                await session.execute(
                    update(Execution)
                    .where(Execution.id == execution_id, Execution.status.in_(list(expected)))
                    .values(status=new, **values)
                    .returning(Execution.chat_id, Execution.exit_code)
                )
            ).one_or_none()
            if row is None:
                return False
            if row.chat_id is not None:
                data: dict[str, JsonValue] = {
                    "execution_id": str(execution_id),
                    "status": new.value,
                    "exit_code": row.exit_code,
                }
                await append_events(session, row.chat_id, [NewEvent("execution", data)])
        return True


class Environment(Protocol):
    """Source of the environment variables credentials grant to a chat's ``code_run``."""

    async def environment(self, execution: Execution) -> dict[str, str]:
        """Return the variables for the run, recording each use; values are secret."""
        ...


class ExecuteRunner:
    """Body of the ``run_execution`` activity on the worker that hosts the run."""

    def __init__(  # noqa: PLR0913 - the collaborators of a run
        self,
        settings: Settings,
        executions: Executions,
        backend: SandboxBackend,
        artifacts: Artifacts,
        workspaces: WorkspaceService,
        *,
        environment: Environment | None = None,
    ) -> None:
        self._limits = settings.execute
        self._host = settings.worker.id
        self._executions = executions
        self._backend = backend
        self._artifacts = artifacts
        self._workspaces = workspaces
        self._environment = environment

    @traced("xavier.execution", EXECUTION_DURATION)
    async def run(self, execution_id: UUID, heartbeat: Heartbeat) -> ExecutionStatus:
        """Run the execution once.

        A run found ``running`` was started by a lost attempt: it becomes ``uncertain`` and is
        not repeated. Errors before the code starts propagate so that the activity retries.
        """
        execution = await self._executions.get(execution_id)
        if execution.status in FINAL_EXECUTION_STATUSES:
            return execution.status
        if execution.kind is ExecutionKind.TERMINAL:
            return await self._follow(execution, heartbeat)
        if execution.status is ExecutionStatus.RUNNING:
            await self._executions.settle(execution_id, "the worker running it was lost")
            return (await self._executions.get(execution_id)).status
        beating = asyncio.create_task(_beat(heartbeat))
        try:
            volume = None
            if execution.chat_id is not None:
                active = await self._workspaces.activate(execution.chat_id)
                volume = active.workspace_volume
            spec = SandboxSpec(execution_id, execution.chat_id, volume, execution.network)
            sandbox = await self._backend.create(spec)
            try:
                if not await self._executions.transition(
                    execution_id,
                    {ExecutionStatus.PENDING},
                    ExecutionStatus.RUNNING,
                    host=self._host,
                    container_id=sandbox.id,
                    started_at=func.now(),
                ):
                    return (await self._executions.get(execution_id)).status
                try:
                    return await self._execute(execution, sandbox)
                except asyncio.CancelledError:
                    await self._executions.transition(
                        execution_id,
                        {ExecutionStatus.RUNNING},
                        ExecutionStatus.CANCELLED,
                        error="cancelled",
                    )
                    raise
            finally:
                await sandbox.destroy()
        finally:
            beating.cancel()

    async def _execute(self, execution: Execution, sandbox: Sandbox) -> ExecutionStatus:
        for artifact_id in execution.input_artifact_ids:
            artifact, data = await self._artifacts.read(artifact_id, execution.created_by)
            name = posixpath.basename(artifact.name) or str(artifact.id)
            await sandbox.put(f"{WORKSPACE_MOUNT}/{name}", data)
        if execution.code is not None:
            file_name, interpreter = LANGUAGES[execution.language]
            path = f"{_CODE_DIR}/{file_name}"
            await sandbox.put(path, execution.code.encode())
            argv = [*interpreter, path]
        else:
            argv = ["bash", "-c", execution.command or ""]
        env = await self._env(execution)
        seconds = int(self._limits.max_duration.total_seconds())
        started = time.monotonic()
        result = await sandbox.run(
            ["timeout", "-s", "KILL", str(seconds), *argv],
            limit=int(self._limits.output_limit),
            env=env,
        )
        elapsed = time.monotonic() - started
        stdout = _redact(result.stdout.decode(errors="replace"), env)
        stderr = _redact(result.stderr.decode(errors="replace"), env)
        outputs: list[UUID] = []
        missing: list[JsonValue] = []
        for path in execution.export:
            data = await sandbox.read(path, limit=MAX_FILE_BYTES)
            if data is None:
                missing.append(path)
                continue
            artifact = await self._artifacts.create(
                chat_id=execution.chat_id,
                name=posixpath.basename(path),
                content_type=mimetypes.guess_type(path)[0] or "application/octet-stream",
                data=data,
                source="output",
                created_by=execution.created_by,
            )
            outputs.append(artifact.id)
        metrics = {**await sandbox.metrics(), "duration_ms": int(elapsed * 1000)}
        if missing:
            metrics["missing_exports"] = missing
        if result.exit_code == _KILLED and elapsed >= seconds - 1:
            status = ExecutionStatus.TIMEOUT
        elif result.exit_code == 0:
            status = ExecutionStatus.SUCCEEDED
        else:
            status = ExecutionStatus.FAILED
        await self._executions.transition(
            execution.id,
            {ExecutionStatus.RUNNING},
            status,
            exit_code=result.exit_code,
            stdout=stdout,
            stderr=stderr,
            truncated=result.truncated,
            output_artifact_ids=outputs,
            metrics=metrics,
        )
        return status

    async def _env(self, execution: Execution) -> dict[str, str]:
        """Return the credential variables of a chat's ``code_run``; API runs get none."""
        if (
            self._environment is None
            or execution.chat_id is None
            or execution.action_id is None
            or execution.kind is not ExecutionKind.CODE
        ):
            return {}
        return await self._environment.environment(execution)

    async def _follow(self, job: Execution, heartbeat: Heartbeat) -> ExecutionStatus:
        """Follow a terminal job until it ends; cancellation terminates it.

        Jobs live in the Computer's daemon: when the Computer stops, so does the job, and the
        execution becomes ``uncertain``. Following again after a lost attempt is safe.
        """
        chat_id = job.chat_id
        if chat_id is None:
            raise TypeError("terminal jobs belong to chats")
        request: dict[str, JsonValue] = {"job_id": str(job.id), "wait_s": _HEARTBEAT_SECONDS}
        try:
            while True:
                heartbeat()
                computer = await self._workspaces.running_computer(chat_id)
                try:
                    view = (
                        await computer.call("job.wait", request, timeout=_JOB_CALL_TIMEOUT)
                        if computer is not None
                        else None
                    )
                except ComputerError as error:
                    if error.code != "not_found":
                        raise
                    view = None
                if view is None:
                    await self._executions.settle(job.id, "the job ended with its Computer")
                    return (await self._executions.get(job.id)).status
                if view.get("job_status") == "finished":
                    return await self._finish_job(job.id, view)
        except asyncio.CancelledError:
            computer = await self._workspaces.running_computer(chat_id)
            if computer is not None:
                with contextlib.suppress(ComputerError):
                    view = await computer.call(
                        "job.cancel", {"job_id": str(job.id)}, timeout=_JOB_CALL_TIMEOUT
                    )
                    await self._finish_job(job.id, view, cancelled=True)
            raise

    async def _finish_job(
        self, job_id: UUID, view: dict[str, JsonValue], *, cancelled: bool = False
    ) -> ExecutionStatus:
        exit_code = view.get("exit_code")
        if cancelled:
            status = ExecutionStatus.CANCELLED
        else:
            status = ExecutionStatus.SUCCEEDED if exit_code == 0 else ExecutionStatus.FAILED
        await self._executions.transition(
            job_id,
            {ExecutionStatus.RUNNING},
            status,
            exit_code=exit_code if isinstance(exit_code, int) else None,
            stdout=str(view.get("output", "")),
            truncated=view.get("truncated") is True,
        )
        return (await self._executions.get(job_id)).status


def _redact(text: str, env: dict[str, str]) -> str:
    """Replace credential values in the output; code that transforms them is not caught."""
    for value in sorted(env.values(), key=len, reverse=True):
        if value:
            text = text.replace(value, REDACTED)
    return text


async def _owner(session: AsyncSession, chat_id: UUID | None, created_by: UUID | None) -> UUID:
    """Return the owner of a run: the chat's owner, else ``created_by``."""
    owner = created_by
    if chat_id is not None:
        owner = await session.scalar(select(Chat.created_by).where(Chat.id == chat_id))
    if owner is None:
        raise NotFoundError(f"chat {chat_id} not found")
    return owner


async def _beat(heartbeat: Heartbeat) -> None:
    while True:
        heartbeat()
        await asyncio.sleep(_HEARTBEAT_SECONDS)
