"""Workspace, Execute and the Computer against Docker, with the sandbox firewall.

docker build -t xavier-sandbox:local apps/server/images/sandbox
docker build -t xavier-desktop:local apps/server/images/desktop
docker compose run --rm firewall-init

The Computer tests open https://example.com, so they need internet access.
"""

import asyncio
import base64
import contextlib
import itertools
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

import aiodocker
import mcp_types as mt
import pytest
from pydantic import JsonValue
from temporalio.client import Client
from temporalio.worker import Worker
from types_aiobotocore_s3.client import S3Client

from src.agent.gateway import tool_result
from src.compute.channel import ComputerError
from src.compute.docker_backend import DockerComputerBackend
from src.compute.pool import ComputerPool
from src.compute.service import ComputeService
from src.config import ExecuteSettings, Settings, WorkspaceSettings
from src.credentials.service import AgentCredentials
from src.db.engine import Sessions, create_engine, create_sessions, migrate
from src.db.models import Chat, ChatStatus, Execution, ExecutionStatus, WorkspaceStatus
from src.docker.client import DockerHost
from src.docker.firewall_probe import check_firewall
from src.events import read_events
from src.execute.docker_backend import DockerSandboxBackend
from src.execute.service import ExecuteRunner, ExecutionRequest, Executions, workflow_id
from src.leases import Leases
from src.observe.credentials import Vault
from src.observe.fetch import BrowserRenderer, HttpFetcher
from src.observe.service import Sources
from src.orchestration.activities import WorkspaceActivities
from src.orchestration.client import connect_temporal
from src.orchestration.models import host_queue
from src.orchestration.workflows import ExecutionWorkflow
from src.policy.model import Mode, Risk
from src.policy.redact import Redactor
from src.storage.artifacts import Artifacts
from src.storage.blobs import S3Adapter
from src.tools.base import Services, ToolContext
from src.tools.compute import (
    Browser,
    BrowserAction,
    BrowserArgs,
    Screen,
    ScreenArgs,
    Terminal,
    TerminalArgs,
)
from src.workspace.service import WorkspaceError, WorkspaceService, mark_lost
from tests.support import add_user

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

RUN_WAIT_SECONDS = 120.0


@dataclass
class Host:
    """One worker: its settings, Docker objects and services."""

    settings: Settings
    sessions: Sessions
    docker: DockerHost
    workspaces: WorkspaceService
    executions: Executions
    runner: ExecuteRunner
    artifacts: Artifacts
    pool: ComputerPool
    compute: ComputeService
    renderer: BrowserRenderer
    sources: Sources


type MakeHost = Callable[..., Host]


@pytest.fixture
async def sessions(settings: Settings) -> AsyncIterator[Sessions]:
    await asyncio.to_thread(migrate, settings.database)
    engine = create_engine(settings.database)
    try:
        yield create_sessions(engine)
    finally:
        await engine.dispose()


@pytest.fixture
async def client(s3: S3Client, settings: Settings) -> Client:
    return await connect_temporal(settings, s3)


@pytest.fixture
async def make_host(
    settings: Settings, sessions: Sessions, s3: S3Client, client: Client
) -> AsyncIterator[MakeHost]:
    docker = aiodocker.Docker()
    task_queue = f"test-execute-{uuid.uuid4()}"
    hosts: list[Host] = []

    def make(
        execute: dict[str, object] | None = None, size_limit: str = "1GiB", pool_size: int = 0
    ) -> Host:
        configured = settings.model_copy(
            update={
                "worker": settings.worker.model_copy(
                    update={"id": f"test-{uuid.uuid4().hex[:12]}"}
                ),
                "temporal": settings.temporal.model_copy(update={"task_queue": task_queue}),
                "execute": ExecuteSettings.model_validate(
                    {"max_duration": "PT30S", **(execute or {})}
                ),
                "workspace": WorkspaceSettings.model_validate({"size_limit": size_limit}),
            }
        )
        host = DockerHost(docker, configured.worker.id)
        blobs = S3Adapter(s3, configured.s3.artifacts_bucket)
        artifacts = Artifacts(sessions, blobs)
        computers = DockerComputerBackend(host, configured.computer)
        pool = ComputerPool(computers, host, pool_size)
        leases = Leases(sessions)
        workspaces = WorkspaceService(
            configured, sessions, host, s3, artifacts, pool=pool, leases=leases
        )
        executions = Executions(configured, sessions, client)
        runner = ExecuteRunner(
            configured,
            executions,
            DockerSandboxBackend(host, configured.execute),
            artifacts,
            workspaces,
        )
        hosts.append(
            Host(
                configured,
                sessions,
                host,
                workspaces,
                executions,
                runner,
                artifacts,
                pool,
                ComputeService(leases),
                BrowserRenderer(computers, host),
                Sources(sessions, blobs),
            )
        )
        return hosts[-1]

    try:
        await DockerHost(docker, "test").ensure_network()
        yield make
    finally:
        for host in hosts:
            containers, volumes = await host.docker.owned()
            for container_id, _labels in containers:
                await host.docker.remove_container(container_id)
            for name, _labels in volumes:
                await host.docker.remove_volume(name)
        await docker.close()


type Serve = Callable[[Host], Awaitable[Host]]


@pytest.fixture
async def serving(client: Client, sessions: Sessions, make_host: MakeHost) -> AsyncIterator[Serve]:
    """Run Temporal workers of a host on the shared queue and on its host queue."""
    del make_host  # its cleanup must run after the workers stop
    async with contextlib.AsyncExitStack() as stack:

        async def serve(host: Host) -> Host:
            activities = WorkspaceActivities(
                sessions,
                host.workspaces,
                host.executions,
                host.runner,
                host.settings.temporal.task_queue,
            ).all()
            queue = host.settings.temporal.task_queue
            own = host_queue(queue, host.docker.worker_id)
            await stack.enter_async_context(
                Worker(
                    client, task_queue=queue, workflows=[ExecutionWorkflow], activities=activities
                )
            )
            await stack.enter_async_context(Worker(client, task_queue=own, activities=activities))
            return host

        yield serve


async def _chat(sessions: Sessions, *, network: bool = False) -> Chat:
    chat = Chat(
        id=uuid.uuid7(),
        created_by=await add_user(sessions),
        title="t",
        status=ChatStatus.READY,
        model="test",
        reasoning="off",
        mode=Mode.AUTONOMOUS,
        tool_groups=["execute", "compute", "files"],
        execute_network=network,
        budget_usd=1.0,
        max_steps=50,
    )
    async with sessions.begin() as session:
        session.add(chat)
    return chat


async def _run(
    host: Host,
    request: ExecutionRequest,
    chat_id: UUID | None,
    *,
    created_by: UUID | None = None,
) -> Execution:
    queue = host.settings.temporal.task_queue
    if chat_id is not None:
        queue = host_queue(queue, host.docker.worker_id)
    execution_id = await host.executions.start(
        request, task_queue=queue, chat_id=chat_id, created_by=created_by
    )
    return await host.executions.wait(execution_id, RUN_WAIT_SECONDS)


def _code(code: str, *, export: tuple[str, ...] = ()) -> ExecutionRequest:
    return ExecutionRequest(
        language="python", code=code, command=None, network=False, export=export
    )


def _command(command: str, *, export: tuple[str, ...] = ()) -> ExecutionRequest:
    return ExecutionRequest(
        language="bash", code=None, command=command, network=False, export=export
    )


async def test_files_persist_between_runs_and_exports_become_artifacts(
    make_host: MakeHost, serving: Serve, sessions: Sessions
) -> None:
    host = await serving(make_host())
    chat = await _chat(sessions)
    chat_id = chat.id

    first = await _run(host, _command("mkdir -p data && echo 1,2 > data/in.csv"), chat_id)
    second = await _run(
        host,
        _code(
            "rows = open('data/in.csv').read()\nopen('out.csv', 'w').write(rows * 2)\nprint('ok')",
            export=("/workspace/out.csv",),
        ),
        chat_id,
    )

    assert first.status is ExecutionStatus.SUCCEEDED
    assert second.status is ExecutionStatus.SUCCEEDED
    assert second.stdout == "ok\n"
    [artifact_id] = second.output_artifact_ids
    artifact, data = await host.artifacts.read(artifact_id, chat.created_by)
    assert (artifact.chat_id, artifact.name, data) == (chat_id, "out.csv", b"1,2\n1,2\n")


async def test_time_memory_and_output_limits(
    make_host: MakeHost, serving: Serve, sessions: Sessions
) -> None:
    host = await serving(
        make_host({"max_duration": "PT3S", "memory": "128MiB", "output_limit": "1KiB"})
    )
    owner = await add_user(sessions)

    slow = await _run(host, _code("import time\ntime.sleep(30)"), None, created_by=owner)
    hungry = await _run(
        host, _code("data = b'x' * (512 << 20)\nprint(len(data))"), None, created_by=owner
    )
    chatty = await _run(host, _code("print('x' * 100_000)"), None, created_by=owner)

    assert slow.status is ExecutionStatus.TIMEOUT
    assert (hungry.status, hungry.exit_code) == (ExecutionStatus.FAILED, 137)
    assert chatty.status is ExecutionStatus.SUCCEEDED
    assert chatty.truncated
    assert len(chatty.stdout) == 1024


async def test_runs_see_no_host_files_secrets_or_privileges(
    make_host: MakeHost, serving: Serve, sessions: Sessions
) -> None:
    host = await serving(make_host())
    script = (
        "id -u; grep CapEff /proc/self/status; "
        "test -e /var/run/docker.sock && echo DOCKER_SOCKET; "
        "test -e /etc/xavier && echo XAVIER_CONFIG; "
        "touch /usr/probe 2>/dev/null && echo ROOT_WRITABLE; env"
    )

    run = await _run(host, _command(script), None, created_by=await add_user(sessions))

    lines = run.stdout.splitlines()
    assert lines[:2] == ["1000", "CapEff:\t0000000000000000"]
    for marker in ("DOCKER_SOCKET", "XAVIER_CONFIG", "ROOT_WRITABLE"):
        assert marker not in run.stdout
    for secret in host.settings.secret_values():
        assert secret not in run.stdout


async def test_network_is_off_by_default_and_internal_addresses_stay_closed(
    make_host: MakeHost, serving: Serve, sessions: Sessions
) -> None:
    host = await serving(make_host())
    probe = (
        "import socket\ntry:\n    socket.create_connection(('1.1.1.1', 53), timeout=3).close()\n"
        "    print('open')\nexcept OSError:\n    print('blocked')"
    )

    offline = await _run(host, _code(probe), None, created_by=await add_user(sessions))

    assert offline.stdout == "blocked\n"
    await check_firewall(host.docker, host.settings, await host.docker.network_gateway())


async def test_large_export_is_passed_by_reference(
    make_host: MakeHost, serving: Serve, sessions: Sessions, client: Client
) -> None:
    host = await serving(make_host())
    chat = await _chat(sessions)
    chat_id = chat.id
    request = _command("head -c 16M /dev/urandom > big.bin", export=("/workspace/big.bin",))

    run = await _run(host, request, chat_id)

    [artifact_id] = run.output_artifact_ids
    artifact, _data = await host.artifacts.read(artifact_id, chat.created_by)
    assert artifact.size_bytes == 16 * 1024 * 1024
    history = await client.get_workflow_handle(workflow_id(run.id)).fetch_history()
    assert len(history.to_json()) < 64 * 1024


async def test_checkpoint_restores_on_another_worker(
    make_host: MakeHost, sessions: Sessions
) -> None:
    first, second = make_host(), make_host()
    chat_id = (await _chat(sessions)).id
    active = await first.workspaces.activate(chat_id)
    await first.workspaces.write_file(active, "/workspace/notes/a.txt", b"kept")

    assert await first.workspaces.hibernate(chat_id)
    restored = await second.workspaces.activate(chat_id)

    assert restored.restored_at is not None
    assert await second.workspaces.read_file(restored, "/workspace/notes/a.txt", 100) == (
        b"kept",
        4,
    )
    assert (await first.docker.owned()) == ([], [])
    await second.workspaces.delete(chat_id)
    row = await second.workspaces.get(chat_id)
    assert row is not None
    assert row.status is WorkspaceStatus.DELETED


async def test_workspace_over_the_size_limit_is_full(
    make_host: MakeHost, sessions: Sessions
) -> None:
    host = make_host(size_limit="1MiB")
    active = await host.workspaces.activate((await _chat(sessions)).id)
    await host.workspaces.write_file(active, "/workspace/big.bin", b"x" * (2 << 20))

    with pytest.raises(WorkspaceError) as raised:
        await host.workspaces.ensure_room(active)

    assert raised.value.code == "workspace_full"


async def test_lost_host_leaves_runs_uncertain_and_restores_the_checkpoint(
    make_host: MakeHost, sessions: Sessions
) -> None:
    lost, survivor = make_host(), make_host()
    chat = await _chat(sessions)
    chat_id = chat.id
    active = await lost.workspaces.activate(chat_id)
    await lost.workspaces.write_file(active, "/workspace/a.txt", b"saved")
    assert await lost.workspaces.checkpoint(chat_id)
    execution_id = uuid.uuid7()
    async with sessions.begin() as session:
        session.add(
            Execution(
                id=execution_id,
                created_by=chat.created_by,
                chat_id=chat_id,
                status=ExecutionStatus.RUNNING,
                host=lost.docker.worker_id,
                language="bash",
                command="sleep 600",
                network=False,
                export=[],
                input_artifact_ids=[],
                stdout="",
                stderr="",
                truncated=False,
                output_artifact_ids=[],
                metrics={},
            )
        )

    assert await mark_lost(sessions, chat_id, active.epoch)
    assert not await mark_lost(sessions, chat_id, active.epoch)
    await lost.workspaces.cleanup_orphans()
    restored = await survivor.workspaces.activate(chat_id)

    async with sessions() as session:
        events = await read_events(session, chat_id, None, 100)
    assert {"status": "lost", "host": lost.docker.worker_id} in [event.data for event in events]
    assert (await lost.executions.get(execution_id)).status is ExecutionStatus.UNCERTAIN
    assert (await lost.docker.owned()) == ([], [])
    assert (await survivor.workspaces.read_file(restored, "/workspace/a.txt", 100))[0] == b"saved"
    await survivor.workspaces.delete(chat_id)


async def test_cancelled_run_stops_and_its_container_is_removed(
    make_host: MakeHost, serving: Serve, sessions: Sessions
) -> None:
    host = await serving(make_host())
    queue = host.settings.temporal.task_queue
    execution_id = await host.executions.start(
        _code("import time\ntime.sleep(60)"),
        task_queue=queue,
        created_by=await add_user(sessions),
    )
    async with asyncio.timeout(30):
        while (await host.executions.get(execution_id)).status is ExecutionStatus.PENDING:  # noqa: ASYNC110 - polls the database
            await asyncio.sleep(0.2)

    await host.executions.cancel(execution_id)
    cancelled = await host.executions.wait(execution_id, 30)

    assert cancelled.status is ExecutionStatus.CANCELLED
    assert (await host.docker.owned())[0] == []


def _tools(host: Host, chat: Chat, *, risk: Risk = Risk.WRITE) -> ToolContext:
    return ToolContext(
        chat_id=chat.id,
        owner=chat.created_by,
        action_id=uuid.uuid7(),
        mode=Mode.AUTONOMOUS,
        execute_network=False,
        image_input=True,
        services=Services(
            settings=host.settings,
            sessions=host.sessions,
            artifacts=host.artifacts,
            fetcher=HttpFetcher(),
            renderer=host.renderer,
            sources=host.sources,
            workspaces=host.workspaces,
            executions=host.executions,
            compute=host.compute,
            credentials=AgentCredentials(
                host.sessions,
                Vault(host.settings.observe.credentials_key.get_secret_value()),
                Redactor([]),
            ),
            host_queue=host_queue(host.settings.temporal.task_queue, host.docker.worker_id),
        ),
        risk=risk,
    )


async def _terminal(host: Host, chat: Chat, command: str) -> str:
    result = await Terminal().run(TerminalArgs(command=command, wait_s=30), _tools(host, chat))
    assert result.data["exit_code"] == 0, result.data
    return str(result.data["output"])


async def _browse(host: Host, chat: Chat, *actions: BrowserAction) -> dict[str, JsonValue]:
    tool = Browser()
    result = await tool.run(tool.prepare(BrowserArgs(actions=list(actions))), _tools(host, chat))
    steps = result.data["results"]
    assert isinstance(steps, list)
    assert all(isinstance(step, dict) and step["ok"] for step in steps), result.data
    snapshot = result.data["snapshot"]
    assert isinstance(snapshot, dict)
    return snapshot


EXAMPLE = "https://example.com/"


async def test_browser_terminal_and_code_runs_share_the_workspace(
    make_host: MakeHost, serving: Serve, sessions: Sessions
) -> None:
    host = await serving(make_host())
    chat = await _chat(sessions)
    chat_id = chat.id

    page = await _browse(host, chat, BrowserAction(action="navigate", url=EXAMPLE))
    await _terminal(host, chat, "echo from-terminal > /workspace/t.txt; touch ~/private")
    run = await _run(
        host,
        _command(
            "cat /workspace/t.txt; echo from-run > /workspace/r.txt; "
            "test -e /home/user/private && echo HOME_VISIBLE; true"
        ),
        chat_id,
    )
    seen = await _terminal(host, chat, "cat /workspace/r.txt")

    assert page["title"] == "Example Domain"
    assert run.stdout == "from-terminal\n"
    assert seen == "from-run\n"
    await host.workspaces.delete(chat_id)


async def test_screenshot_reaches_the_model_as_an_image(
    make_host: MakeHost, sessions: Sessions
) -> None:
    host = make_host()
    chat = await _chat(sessions)
    chat_id = chat.id
    ctx = _tools(host, chat, risk=Risk.READ)

    result = await Screen().run(ScreenArgs(action="screenshot", marks=True), ctx)
    mcp = tool_result(result.data, result.images)

    [_text, image] = mcp.content
    assert isinstance(image, mt.ImageContent)
    assert image.mime_type == "image/jpeg"
    assert base64.b64decode(image.data)[:2] == b"\xff\xd8"
    assert (result.data["width"], result.data["height"]) == (1280, 800)
    await host.workspaces.delete(chat_id)


async def test_gui_calls_never_overlap_and_a_stale_token_is_rejected(
    make_host: MakeHost, sessions: Sessions
) -> None:
    host = make_host()
    chat_id = (await _chat(sessions)).id
    computer = (await host.workspaces.activate(chat_id, computer=True)).computer
    assert computer is not None
    other_worker = ComputeService(Leases(sessions))
    spans: list[tuple[float, float]] = []

    async def gui_call(compute: ComputeService, number: int) -> int:
        async with compute.gui(chat_id, f"action:{number}") as token:
            started = time.monotonic()
            await computer.call("browser", {"actions": [], "snapshot": True}, token=token)
            spans.append((started, time.monotonic()))
            return token

    tokens = await asyncio.gather(
        *(gui_call(host.compute if n % 2 else other_worker, n) for n in range(4))
    )

    spans.sort()
    assert all(end <= next_start for (_s, end), (next_start, _e) in itertools.pairwise(spans))
    assert sorted(tokens) == list(range(min(tokens), min(tokens) + 4))
    with pytest.raises(ComputerError) as raised:
        await computer.call("browser", {"actions": []}, token=min(tokens))
    assert raised.value.code == "stale_token"
    await host.workspaces.delete(chat_id)


async def test_chromium_profile_survives_hibernation(
    make_host: MakeHost, sessions: Sessions
) -> None:
    host = make_host()
    chat = await _chat(sessions)
    chat_id = chat.id
    await _browse(host, chat, BrowserAction(action="navigate", url=EXAMPLE))

    assert await host.workspaces.hibernate(chat_id)
    restored = await _browse(host, chat)

    assert restored["url"] == EXAMPLE
    await host.workspaces.delete(chat_id)


async def test_new_workspace_takes_a_ready_computer_from_the_pool(
    make_host: MakeHost, sessions: Sessions
) -> None:
    host = make_host(pool_size=1)
    refill = asyncio.create_task(host.pool.run())
    try:
        async with asyncio.timeout(60):
            while not host.pool.ready_count:  # noqa: ASYNC110 - the pool has no ready signal
                await asyncio.sleep(0.2)
        chat_id = (await _chat(sessions)).id

        started = time.monotonic()
        active = await host.workspaces.activate(chat_id, computer=True)
        elapsed = time.monotonic() - started

        assert active.computer is not None
        assert active.workspace_volume.startswith("xv-pool-ws-")
        assert elapsed < 10
        await host.workspaces.delete(chat_id)
    finally:
        refill.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await refill


async def test_a_removed_pool_computer_is_not_handed_out(
    make_host: MakeHost, sessions: Sessions
) -> None:
    host = make_host(pool_size=1)
    refill = asyncio.create_task(host.pool.run())
    try:
        async with asyncio.timeout(60):
            while not host.pool.ready_count:  # noqa: ASYNC110 - the pool has no ready signal
                await asyncio.sleep(0.2)
        for container_id, _ in (await host.docker.owned())[0]:
            await host.docker.remove_container(container_id)
        chat_id = (await _chat(sessions)).id

        active = await host.workspaces.activate(chat_id, computer=True)

        assert active.computer is not None
        assert (await active.computer.call("ping"))["browser"]
        await host.workspaces.delete(chat_id)
    finally:
        refill.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await refill


async def test_a_computer_removed_under_an_active_workspace_is_replaced(
    make_host: MakeHost, sessions: Sessions
) -> None:
    host = make_host()
    chat_id = (await _chat(sessions)).id
    first = (await host.workspaces.activate(chat_id, computer=True)).computer
    assert first is not None
    await host.docker.remove_container(first.id)

    second = (await host.workspaces.activate(chat_id, computer=True)).computer

    assert second is not None
    assert second.id != first.id
    assert (await second.call("ping"))["browser"]
    await host.workspaces.delete(chat_id)


async def test_leases_fence_every_new_holder(sessions: Sessions) -> None:
    leases, key = Leases(sessions), f"test:{uuid.uuid7()}"

    first = await leases.acquire(key, "a", timedelta(minutes=1))
    refused = await leases.acquire(key, "b", timedelta(minutes=1))
    await leases.release(key, "a", 999)
    still = await leases.current(key)
    await leases.release(key, "a", first or 0)
    second = await leases.acquire(key, "b", timedelta(milliseconds=1))
    await asyncio.sleep(0.05)
    third = await leases.acquire(key, "c", timedelta(minutes=1))

    assert (first, refused, second, third) == (1, None, 2, 3)
    assert still is not None
    assert still.holder == "a"
    assert not await leases.renew(key, "b", 2, timedelta(minutes=1))
    assert await leases.renew(key, "c", 3, timedelta(minutes=1))
