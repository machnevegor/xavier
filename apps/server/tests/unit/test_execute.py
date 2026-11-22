import asyncio
import uuid
from collections.abc import Collection, Mapping, Sequence
from datetime import timedelta
from uuid import UUID

import pytest
from pydantic import JsonValue

from src.compute.backend import Computer
from src.config import Settings
from src.db.models import Artifact, Execution, ExecutionKind, ExecutionStatus
from src.docker.client import ExecResult
from src.errors import NotFoundError
from src.execute.backend import Sandbox, SandboxSpec
from src.execute.service import (
    ExecuteRunner,
    ExecutionRequest,
    Executions,
    execution_start,
)
from src.policy.model import Mode, Risk
from src.policy.redact import REDACTED
from src.policy.urls import ResourceRejectedError
from src.storage.artifacts import Artifacts
from src.tools.base import ToolContext, ToolError
from src.tools.execute import CodeRun, CodeRunArgs
from src.tools.files import EDIT_MAX_BYTES, Files, FilesArgs
from src.tools.jobs import Job, JobArgs
from src.workspace.service import Active, WorkspaceError, WorkspaceService
from tests.fakes import services as tool_services

SETTINGS = Settings.model_validate(
    {
        "worker": {"id": "worker-a", "internal_token": "internal"},
        "database": {"password": "db"},
        "s3": {"access_key_id": "id", "secret_access_key": "secret"},
        "observe": {"credentials_key": "vBEIpEgikO_ICioUfkcoQ2wQc9mtSBVhuxtZW05nJqM="},
        "agent": {
            "providers": {"main": {"base_url": "https://llm.example/v1", "api_key": "key"}},
            "models": [
                {
                    "id": "small",
                    "provider": "main",
                    "provider_model": "vendor/small",
                    "pricing_per_mtok": {"input": 0.1, "output": 0.2},
                }
            ],
            "default_model": "small",
        },
        "execute": {"max_duration": "PT5S", "output_limit": 16},
    }
)
HOST_QUEUE = "xavier.host.worker-a"
CODE_PATH = "/tmp/xavier/main.py"  # noqa: S108 - a path inside the sandbox
CHAT = uuid.uuid4()
OWNER = uuid.uuid4()


def _execution(**values: object) -> Execution:
    defaults: dict[str, object] = {
        "id": uuid.uuid4(),
        "created_by": OWNER,
        "chat_id": CHAT,
        "action_id": None,
        "kind": ExecutionKind.CODE,
        "status": ExecutionStatus.PENDING,
        "language": "python",
        "code": "print(1)",
        "command": None,
        "network": False,
        "export": [],
        "input_artifact_ids": [],
        "stdout": "",
        "stderr": "",
        "truncated": False,
        "output_artifact_ids": [],
        "metrics": {},
    }
    return Execution(**(defaults | values))


class FakeExecutions(Executions):
    def __init__(self, *rows: Execution) -> None:
        self.rows = {row.id: row for row in rows}
        self.started: list[tuple[ExecutionRequest, str, UUID | None]] = []

    async def get(self, execution_id: UUID) -> Execution:
        return self.rows[execution_id]

    async def transition(
        self,
        execution_id: UUID,
        expected: Collection[ExecutionStatus],
        new: ExecutionStatus,
        **values: object,
    ) -> bool:
        row = self.rows[execution_id]
        if row.status not in expected:
            return False
        row.status = new
        for key, value in values.items():
            setattr(row, key, value)
        return True

    async def start(
        self,
        request: ExecutionRequest,
        *,
        task_queue: str,
        chat_id: UUID | None = None,
        action_id: UUID | None = None,
        created_by: UUID | None = None,
    ) -> UUID:
        del action_id, created_by
        self.started.append((request, task_queue, chat_id))
        row = _execution(chat_id=chat_id, status=ExecutionStatus.SUCCEEDED, stdout="done")
        self.rows[row.id] = row
        return row.id

    async def wait(self, execution_id: UUID, within: float) -> Execution:
        del within
        return self.rows[execution_id]

    async def until_done(
        self, execution_ids: Collection[UUID], within: float, *, first: bool = False
    ) -> None:
        del execution_ids, within, first

    async def collect(self, execution_ids: Collection[UUID]) -> None:
        for execution_id in execution_ids:
            row = self.rows.get(execution_id)
            if row is not None and row.collected_at is None:
                row.collected_at = row.finished_at


class FakeSandbox:
    def __init__(self, result: ExecResult | None = None, *, hang: bool = False) -> None:
        self.id = "container"
        self.files: dict[str, bytes] = {}
        self.commands: list[Sequence[str]] = []
        self.envs: list[Mapping[str, str] | None] = []
        self.destroyed = False
        self._result = result or ExecResult(0, b"ok\n", b"", truncated=False)
        self._hang = hang

    async def put(self, path: str, data: bytes) -> None:
        self.files[path] = data

    async def run(
        self, command: Sequence[str], *, limit: int, env: Mapping[str, str] | None = None
    ) -> ExecResult:
        del limit
        self.commands.append(command)
        self.envs.append(env)
        if self._hang:
            await asyncio.Event().wait()
        return self._result

    async def read(self, path: str, *, limit: int) -> bytes | None:
        del limit
        return self.files.get(path)

    async def metrics(self) -> dict[str, JsonValue]:
        return {"memory_bytes": 1}

    async def destroy(self) -> None:
        self.destroyed = True


class FakeBackend:
    def __init__(self, sandbox: FakeSandbox | None = None, *, fail: bool = False) -> None:
        self.sandbox = sandbox or FakeSandbox()
        self.specs: list[SandboxSpec] = []
        self._fail = fail

    async def create(self, spec: SandboxSpec) -> Sandbox:
        self.specs.append(spec)
        if self._fail:
            raise RuntimeError("image pull failed")
        return self.sandbox


class FakeWorkspaces(WorkspaceService):
    def __init__(self, files: dict[str, bytes] | None = None) -> None:
        self.touched: list[UUID] = []
        self.files = files or {}

    async def read_file(self, active: Active, path: str, limit: int) -> tuple[bytes, int]:
        del active
        try:
            data = self.files[path]
        except KeyError:
            raise WorkspaceError("not_found", f"{path} is not a file") from None
        return data[:limit], len(data)

    async def write_file(self, active: Active, path: str, data: bytes) -> None:
        del active
        self.files[path] = data

    async def activate(self, chat_id: UUID, *, computer: bool = False) -> Active:
        del computer
        return Active(chat_id, 1, "xv-ws", "xv-home", "holder")

    async def running_computer(self, chat_id: UUID) -> Computer | None:
        del chat_id

    async def touch(self, chat_id: UUID) -> None:
        self.touched.append(chat_id)

    async def ensure_room(self, active: Active) -> None:
        del active


class FakeArtifacts(Artifacts):
    def __init__(self, *stored: tuple[Artifact, bytes]) -> None:
        self.created: list[Artifact] = []
        self.stored = {artifact.id: (artifact, data) for artifact, data in stored}

    async def read(self, artifact_id: UUID, owner: UUID) -> tuple[Artifact, bytes]:
        if artifact_id not in self.stored or self.stored[artifact_id][0].created_by != owner:
            raise NotFoundError(f"artifact {artifact_id} not found")
        return self.stored[artifact_id]

    async def create(  # noqa: PLR0913 - mirrors Artifacts.create
        self,
        *,
        chat_id: UUID | None,
        name: str,
        content_type: str,
        data: bytes,
        source: str,
        created_by: UUID | None = None,
    ) -> Artifact:
        artifact = Artifact(
            id=uuid.uuid4(),
            created_by=created_by,
            chat_id=chat_id,
            name=name,
            content_type=content_type,
            size_bytes=len(data),
            sha256="",
            source=source,
        )
        self.created.append(artifact)
        return artifact


def _runner(executions: Executions, backend: FakeBackend) -> ExecuteRunner:
    return ExecuteRunner(SETTINGS, executions, backend, FakeArtifacts(), FakeWorkspaces())


def _beat() -> None:
    pass


def test_runs_from_a_chat_are_bound_to_the_workspace_host() -> None:
    start = execution_start(SETTINGS, uuid.uuid4(), HOST_QUEUE)

    assert start.task_queue == HOST_QUEUE
    assert start.host_bound
    assert start.max_duration == timedelta(seconds=5)


def test_runs_from_the_api_go_to_the_shared_queue() -> None:
    start = execution_start(SETTINGS, uuid.uuid4(), SETTINGS.temporal.task_queue)

    assert start.task_queue == "xavier"
    assert not start.host_bound


async def test_code_run_starts_on_this_workers_host_queue() -> None:
    executions = FakeExecutions()
    workspaces = FakeWorkspaces()
    ctx = _context(executions, workspaces, execute_network=False)

    result = await CodeRun().run(CodeRunArgs(code="print(1)"), ctx)

    [(request, task_queue, chat_id)] = executions.started
    assert task_queue == HOST_QUEUE
    assert chat_id == CHAT
    assert request.code == "print(1)"
    assert result.data["job_status"] == "succeeded"
    assert workspaces.touched == [CHAT]


async def test_code_run_refuses_network_the_chat_does_not_allow() -> None:
    executions = FakeExecutions()
    ctx = _context(executions, FakeWorkspaces(), execute_network=False)

    with pytest.raises(ToolError) as raised:
        await CodeRun().run(CodeRunArgs(code="print(1)", network=True), ctx)

    assert raised.value.code == "denied"
    assert executions.started == []


def test_code_run_needs_exactly_one_program() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        CodeRunArgs(code="print(1)", command="ls")
    with pytest.raises(ValueError, match="exactly one"):
        CodeRunArgs()


async def test_successful_run_records_output_and_destroys_the_sandbox() -> None:
    execution = _execution(export=["/workspace/out.csv", "/workspace/missing.txt"])
    executions = FakeExecutions(execution)
    backend = FakeBackend()
    backend.sandbox.files["/workspace/out.csv"] = b"a,b\n"

    status = await _runner(executions, backend).run(execution.id, _beat)

    assert status is ExecutionStatus.SUCCEEDED
    assert execution.exit_code == 0
    assert execution.stdout == "ok\n"
    assert len(execution.output_artifact_ids) == 1
    assert isinstance(execution.metrics, dict)
    assert execution.metrics["missing_exports"] == ["/workspace/missing.txt"]
    assert backend.sandbox.files[CODE_PATH] == b"print(1)"
    assert backend.sandbox.commands == [["timeout", "-s", "KILL", "5", "python3", CODE_PATH]]
    assert backend.specs[0].workspace_volume == "xv-ws"
    assert backend.sandbox.destroyed


async def test_api_run_gets_no_workspace_volume() -> None:
    execution = _execution(chat_id=None, language="bash", code=None, command="ls")
    backend = FakeBackend()

    await _runner(FakeExecutions(execution), backend).run(execution.id, _beat)

    assert backend.specs[0].workspace_volume is None
    assert backend.sandbox.commands == [["timeout", "-s", "KILL", "5", "bash", "-c", "ls"]]


async def test_nonzero_exit_fails_the_run() -> None:
    execution = _execution()
    backend = FakeBackend(FakeSandbox(ExecResult(1, b"", b"Traceback", truncated=False)))

    status = await _runner(FakeExecutions(execution), backend).run(execution.id, _beat)

    assert status is ExecutionStatus.FAILED
    assert execution.stderr == "Traceback"


async def test_sandbox_creation_failure_propagates_for_a_retry() -> None:
    execution = _execution()

    with pytest.raises(RuntimeError, match="image pull failed"):
        await _runner(FakeExecutions(execution), FakeBackend(fail=True)).run(execution.id, _beat)

    assert execution.status is ExecutionStatus.PENDING


async def test_run_found_running_becomes_uncertain_without_repeating() -> None:
    execution = _execution(status=ExecutionStatus.RUNNING)
    backend = FakeBackend()

    status = await _runner(FakeExecutions(execution), backend).run(execution.id, _beat)

    assert status is ExecutionStatus.UNCERTAIN
    assert backend.specs == []


async def test_finished_run_is_not_repeated() -> None:
    execution = _execution(status=ExecutionStatus.SUCCEEDED)
    backend = FakeBackend()

    status = await _runner(FakeExecutions(execution), backend).run(execution.id, _beat)

    assert status is ExecutionStatus.SUCCEEDED
    assert backend.specs == []


async def test_cancelled_run_is_recorded_and_destroyed() -> None:
    execution = _execution()
    backend = FakeBackend(FakeSandbox(hang=True))
    task = asyncio.create_task(_runner(FakeExecutions(execution), backend).run(execution.id, _beat))
    while not backend.sandbox.commands:  # noqa: ASYNC110 - waits for the fake to start
        await asyncio.sleep(0.01)

    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert execution.status is ExecutionStatus.CANCELLED
    assert backend.sandbox.destroyed


async def test_job_of_another_chat_is_not_found() -> None:
    foreign = _execution(chat_id=uuid.uuid4())
    ctx = _context(FakeExecutions(foreign), FakeWorkspaces(), execute_network=False)

    with pytest.raises(ToolError) as raised:
        await Job().run(JobArgs(job_id=foreign.id, action="cancel"), ctx)

    assert raised.value.code == "not_found"
    assert foreign.status is ExecutionStatus.PENDING


async def test_waiting_is_a_read_and_cancelling_a_write() -> None:
    ctx = _context(FakeExecutions(), FakeWorkspaces(), execute_network=False)
    job_id = uuid.uuid4()

    assert await Job().classify(JobArgs(job_id=job_id), ctx) is Risk.READ
    assert await Job().classify(JobArgs(job_id=job_id, action="cancel"), ctx) is Risk.WRITE


@pytest.mark.parametrize(
    ("args", "risk"),
    [
        (FilesArgs(op="list"), Risk.READ),
        (FilesArgs(op="read"), Risk.READ),
        (FilesArgs(op="export"), Risk.WRITE),
        (FilesArgs(op="import", path="a.txt", artifact_id=uuid.uuid4()), Risk.WRITE),
        (FilesArgs(op="write", path="a.txt", text="hi"), Risk.WRITE),
        (FilesArgs(op="edit", path="a.txt", old="a", new="b"), Risk.WRITE),
    ],
)
async def test_files_risk_follows_the_operation(args: FilesArgs, risk: Risk) -> None:
    ctx = _context(FakeExecutions(), FakeWorkspaces(), execute_network=False)

    assert await Files().classify(args, ctx) is risk


def test_files_import_needs_an_artifact_and_only_import_takes_one() -> None:
    with pytest.raises(ValueError, match="artifact_id"):
        FilesArgs(op="import", path="a.txt")
    with pytest.raises(ValueError, match="artifact_id"):
        FilesArgs(op="read", path="a.txt", artifact_id=uuid.uuid4())


def test_files_write_and_edit_take_only_their_own_fields() -> None:
    with pytest.raises(ValueError, match="text"):
        FilesArgs(op="write", path="a.txt")
    with pytest.raises(ValueError, match="text"):
        FilesArgs(op="read", text="hi")
    with pytest.raises(ValueError, match="old"):
        FilesArgs(op="edit", path="a.txt", new="b")
    with pytest.raises(ValueError, match="new"):
        FilesArgs(op="edit", path="a.txt", old="a")
    with pytest.raises(ValueError, match="old"):
        FilesArgs(op="write", path="a.txt", text="hi", old="a")
    with pytest.raises(ValueError, match="non-empty"):
        FilesArgs(op="edit", path="a.txt", old="", new="b")
    with pytest.raises(ValueError, match="differ"):
        FilesArgs(op="edit", path="a.txt", old="a", new="a")
    with pytest.raises(ValueError, match="file path"):
        FilesArgs(op="write", text="hi")
    with pytest.raises(ValueError, match="file path"):
        FilesArgs(op="edit", path=".", old="a", new="b")
    with pytest.raises(ValueError, match="replace_all"):
        FilesArgs(op="read", replace_all=True)


def test_files_paths_are_confined_to_the_workspace() -> None:
    assert Files().prepare(FilesArgs(op="read", path="data/a.txt")).path == "/workspace/data/a.txt"
    with pytest.raises(ResourceRejectedError):
        Files().prepare(FilesArgs(op="read", path="../etc/passwd"))


async def test_files_read_truncates_text_and_reports_binary() -> None:
    workspaces = FakeWorkspaces({"/workspace/a.txt": b"abcdef", "/workspace/b.bin": b"\x00\x01"})
    ctx = _context(FakeExecutions(), workspaces, execute_network=False)

    text = await Files().run(FilesArgs(op="read", path="/workspace/a.txt", max_chars=4), ctx)
    binary = await Files().run(FilesArgs(op="read", path="/workspace/b.bin"), ctx)

    assert text.data == {"path": "/workspace/a.txt", "size": 6, "text": "abcd", "truncated": True}
    assert binary.data == {"path": "/workspace/b.bin", "size": 2, "binary": True}


async def test_files_import_writes_an_artifact_of_this_chat() -> None:
    own = _artifact(CHAT)
    workspaces = FakeWorkspaces()
    ctx = _context(
        FakeExecutions(), workspaces, execute_network=False, artifacts=FakeArtifacts((own, b"hi"))
    )

    result = await Files().run(
        FilesArgs(op="import", path="/workspace/in.txt", artifact_id=own.id), ctx
    )

    assert result.data == {"path": "/workspace/in.txt", "size": 2}
    assert workspaces.files == {"/workspace/in.txt": b"hi"}


async def test_files_import_refuses_an_artifact_of_another_chat() -> None:
    foreign = _artifact(uuid.uuid4())
    workspaces = FakeWorkspaces()
    ctx = _context(
        FakeExecutions(),
        workspaces,
        execute_network=False,
        artifacts=FakeArtifacts((foreign, b"secret")),
    )

    with pytest.raises(ToolError) as raised:
        await Files().run(
            FilesArgs(op="import", path="/workspace/in.txt", artifact_id=foreign.id), ctx
        )

    assert raised.value.code == "not_found"
    assert workspaces.files == {}


async def test_files_import_refuses_a_loose_artifact_of_another_user() -> None:
    foreign = _artifact(None, owner=uuid.uuid4())
    workspaces = FakeWorkspaces()
    ctx = _context(
        FakeExecutions(),
        workspaces,
        execute_network=False,
        artifacts=FakeArtifacts((foreign, b"secret")),
    )

    with pytest.raises(ToolError) as raised:
        await Files().run(
            FilesArgs(op="import", path="/workspace/in.txt", artifact_id=foreign.id), ctx
        )

    assert raised.value.code == "not_found"
    assert workspaces.files == {}


async def test_files_write_creates_or_replaces_a_text_file() -> None:
    workspaces = FakeWorkspaces({"/workspace/a.txt": b"old"})
    ctx = _context(FakeExecutions(), workspaces, execute_network=False)

    created = await Files().run(FilesArgs(op="write", path="/workspace/b.txt", text="new"), ctx)
    replaced = await Files().run(FilesArgs(op="write", path="/workspace/a.txt", text="next"), ctx)

    assert created.data == {"path": "/workspace/b.txt", "size": 3}
    assert replaced.data == {"path": "/workspace/a.txt", "size": 4}
    assert workspaces.files == {"/workspace/a.txt": b"next", "/workspace/b.txt": b"new"}


async def test_files_edit_replaces_one_match_and_can_delete_it() -> None:
    workspaces = FakeWorkspaces({"/workspace/a.txt": b"say hello"})
    ctx = _context(FakeExecutions(), workspaces, execute_network=False)

    result = await Files().run(
        FilesArgs(op="edit", path="/workspace/a.txt", old="hello", new=""), ctx
    )

    assert result.data == {"path": "/workspace/a.txt", "replacements": 1}
    assert workspaces.files["/workspace/a.txt"] == b"say "


async def test_files_edit_replaces_every_match_when_asked() -> None:
    workspaces = FakeWorkspaces({"/workspace/a.txt": b"aa"})
    ctx = _context(FakeExecutions(), workspaces, execute_network=False)

    result = await Files().run(
        FilesArgs(op="edit", path="/workspace/a.txt", old="a", new="b", replace_all=True), ctx
    )

    assert result.data == {"path": "/workspace/a.txt", "replacements": 2}
    assert workspaces.files["/workspace/a.txt"] == b"bb"


async def test_files_edit_leaves_the_file_unchanged_when_the_match_is_not_unique() -> None:
    workspaces = FakeWorkspaces({"/workspace/a.txt": b"aa"})
    ctx = _context(FakeExecutions(), workspaces, execute_network=False)

    with pytest.raises(ToolError) as raised:
        await Files().run(FilesArgs(op="edit", path="/workspace/a.txt", old="a", new="b"), ctx)

    assert raised.value.code == "invalid_args"
    assert workspaces.files["/workspace/a.txt"] == b"aa"


async def test_files_edit_leaves_the_file_unchanged_when_the_text_is_missing() -> None:
    workspaces = FakeWorkspaces({"/workspace/a.txt": b"hello"})
    ctx = _context(FakeExecutions(), workspaces, execute_network=False)

    with pytest.raises(ToolError) as raised:
        await Files().run(
            FilesArgs(op="edit", path="/workspace/a.txt", old="goodbye", new="x"), ctx
        )

    assert raised.value.code == "invalid_args"
    assert workspaces.files["/workspace/a.txt"] == b"hello"


async def test_files_edit_refuses_binary_invalid_and_oversized_files() -> None:
    cases = {
        "/workspace/bin": b"a\x00b",
        "/workspace/bad": b"\xff",
        "/workspace/big": b"a" * (EDIT_MAX_BYTES + 1),
    }

    for path, content in cases.items():
        workspaces = FakeWorkspaces({path: content})
        ctx = _context(FakeExecutions(), workspaces, execute_network=False)
        with pytest.raises(ToolError) as raised:
            await Files().run(FilesArgs(op="edit", path=path, old="a", new="b"), ctx)
        assert raised.value.code == "invalid_args"
        assert workspaces.files[path] == content


async def test_files_edit_refuses_a_missing_file() -> None:
    workspaces = FakeWorkspaces()
    ctx = _context(FakeExecutions(), workspaces, execute_network=False)

    with pytest.raises(ToolError) as raised:
        await Files().run(FilesArgs(op="edit", path="/workspace/a.txt", old="a", new="b"), ctx)

    assert raised.value.code == "not_found"
    assert workspaces.files == {}


class FakeEnvironment:
    def __init__(self, env: dict[str, str]) -> None:
        self.env = env
        self.asked: list[UUID] = []

    async def environment(self, execution: Execution) -> dict[str, str]:
        self.asked.append(execution.id)
        return self.env


async def test_code_run_gets_credential_variables_and_their_values_are_redacted() -> None:
    execution = _execution(action_id=uuid.uuid4())
    output = b"token=s3cret-value\n"
    backend = FakeBackend(FakeSandbox(ExecResult(1, output, output, truncated=False)))
    environment = FakeEnvironment({"API_TOKEN": "s3cret-value"})
    runner = ExecuteRunner(
        SETTINGS,
        FakeExecutions(execution),
        backend,
        FakeArtifacts(),
        FakeWorkspaces(),
        environment=environment,
    )

    await runner.run(execution.id, _beat)

    assert backend.sandbox.envs == [{"API_TOKEN": "s3cret-value"}]
    assert execution.stdout == execution.stderr == f"token={REDACTED}\n"


@pytest.mark.parametrize(
    "values",
    [
        {"chat_id": None, "language": "bash", "code": None, "command": "env"},
        {"action_id": None},
    ],
    ids=["api_run", "no_action"],
)
async def test_runs_outside_a_chat_code_run_get_no_credential_variables(
    values: dict[str, object],
) -> None:
    execution = _execution(**values)
    backend = FakeBackend()
    environment = FakeEnvironment({"API_TOKEN": "s3cret-value"})
    runner = ExecuteRunner(
        SETTINGS,
        FakeExecutions(execution),
        backend,
        FakeArtifacts(),
        FakeWorkspaces(),
        environment=environment,
    )

    await runner.run(execution.id, _beat)

    assert backend.sandbox.envs == [{}]
    assert environment.asked == []


def _artifact(chat_id: UUID | None, owner: UUID = OWNER) -> Artifact:
    return Artifact(
        id=uuid.uuid4(),
        created_by=owner,
        chat_id=chat_id,
        name="in.txt",
        content_type="text/plain",
        size_bytes=2,
        sha256="",
        source="upload",
    )


def _context(
    executions: Executions,
    workspaces: WorkspaceService,
    *,
    execute_network: bool,
    artifacts: Artifacts | None = None,
) -> ToolContext:
    return ToolContext(
        chat_id=CHAT,
        owner=OWNER,
        action_id=uuid.uuid4(),
        mode=Mode.AUTONOMOUS,
        execute_network=execute_network,
        image_input=False,
        services=tool_services(
            artifacts=artifacts or FakeArtifacts(),
            workspaces=workspaces,
            executions=executions,
            host_queue=HOST_QUEUE,
        ),
    )
