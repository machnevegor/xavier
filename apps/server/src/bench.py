"""``xavier bench``: Compute, Execute and agent figures for ``docs/performance.md``."""

import asyncio
import contextlib
import math
import platform
import time
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

import aiodocker
from pydantic import JsonValue
from sqlalchemy import func, select

from src.compute.docker_backend import DockerComputerBackend
from src.compute.pool import ComputerPool
from src.compute.service import ComputeService
from src.config import Settings
from src.credentials.service import AgentCredentials
from src.db.engine import Sessions, create_engine, create_sessions
from src.db.models import Action, Chat, ChatStatus, Execution, ExecutionStatus, Turn, User
from src.docker.client import DockerHost
from src.execute.docker_backend import DockerSandboxBackend
from src.execute.service import ExecuteRunner, Executions
from src.leases import Leases
from src.observe.credentials import Vault
from src.observe.fetch import BrowserRenderer, HttpFetcher
from src.observe.service import Sources
from src.orchestration.client import connect_temporal
from src.orchestration.models import host_queue
from src.policy.model import Risk
from src.policy.redact import Redactor
from src.storage.artifacts import Artifacts
from src.storage.blobs import S3Adapter, open_s3_client
from src.tools.base import Services, Tool, ToolArgs, ToolContext, ToolResult
from src.tools.compute import (
    Browser,
    BrowserAction,
    BrowserArgs,
    Screen,
    ScreenArgs,
    Terminal,
    TerminalArgs,
)
from src.workspace.service import WorkspaceService

PAGE = "https://example.com/"
REFERENCE_PREFIX = "Reference:"
"""Title prefix of the chats whose agent figures the report summarizes."""
REFERENCE_TASKS = (
    (
        "Reference: Observe — watch https://api.github.com/repos/python/cpython/releases and alert "
        "on a new release."
    ),
    (
        "Reference: Execute — in Python, compute the mean and median of the integers 1 to 1000 and "
        "save them to /workspace/stats.json."
    ),
    (
        "Reference: Compute — open https://quotes.toscrape.com/js/ in the browser and list the "
        "authors of the first five quotes."
    ),
)
_POOL_WAIT_SECONDS = 120.0
_ACTIVATION_SAMPLES = 5
"""Hibernate and wake cycles; each uploads and restores a checkpoint."""


@dataclass
class Report:
    """Measured seconds."""

    iterations: int
    starts: list[tuple[str, float]] = field(default_factory=list)
    actions: dict[str, list[float]] = field(default_factory=dict)
    steps: list[tuple[str, float]] = field(default_factory=list)
    execute: dict[str, list[float]] = field(default_factory=dict)
    agent: list[tuple[str, int, int, int, float]] = field(default_factory=list)
    """Reference chats: title, turns, LLM calls, tool calls and cost in USD."""

    def markdown(self) -> str:
        """Return the report."""
        lines = [
            "# Performance",
            "",
            (
                f"Measured by `xavier bench` on {datetime.now(UTC):%Y-%m-%d %H:%M} UTC, "
                f"{platform.system()} {platform.machine()}, "
                f"{self.iterations} iterations per action. Pages: {PAGE}."
            ),
            "",
            "## Method",
            "",
            (
                "`uv run xavier bench --output docs/performance.md` on a host of the running "
                "stack, with its `settings.yaml`, rewrites this file. It starts Computers and "
                "sandbox containers through the services the worker uses, calls the tools directly "
                "without a model, and removes everything it created. Times are wall-clock; "
                "percentiles are nearest-rank."
            ),
            "",
            "## Computer start",
            "",
            "| Phase | Seconds |",
            "| --- | ---: |",
            *(f"| {name} | {seconds:.2f} |" for name, seconds in self.starts),
            "",
            "## Actions",
            "",
            "Through the tools, with the GUI lease and the channel to `computerd`; no LLM.",
            "",
            "| Action | p50, ms | p95, ms |",
            "| --- | ---: | ---: |",
            *(
                f"| {name} | {_percentile(samples, 0.5) * 1000:.0f} "
                f"| {_percentile(samples, 0.95) * 1000:.0f} |"
                for name, samples in self.actions.items()
            ),
            "",
            f"## Scenario of {len(self.steps)} steps",
            "",
            "| Step | ms |",
            "| --- | ---: |",
            *(f"| {name} | {seconds * 1000:.0f} |" for name, seconds in self.steps),
            f"| **Total** | **{sum(s for _n, s in self.steps) * 1000:.0f}** |",
            "",
            "## Execute",
            "",
            "`code_run` of `print(1)` in a new sandbox container, and workspace wake-ups.",
            "",
            "| Measurement | p50, ms | p95, ms |",
            "| --- | ---: | ---: |",
            *(
                f"| {name} | {_percentile(samples, 0.5) * 1000:.0f} "
                f"| {_percentile(samples, 0.95) * 1000:.0f} |"
                for name, samples in self.execute.items()
            ),
            "",
            "## Agent",
            "",
            (
                f"Chats titled `{REFERENCE_PREFIX} …`, run through the API or the Web UI before "
                "the bench with these first messages:"
            ),
            "",
            *(f"- {task}" for task in REFERENCE_TASKS),
            "",
            *(
                [
                    "| Chat | Turns | LLM calls | Tool calls | Cost, USD |",
                    "| --- | ---: | ---: | ---: | ---: |",
                    *(
                        f"| {title} | {turns} | {steps} | {calls} | {cost:.4f} |"
                        for title, turns, steps, calls, cost in self.agent
                    ),
                ]
                if self.agent
                else ["No reference chats yet."]
            ),
            "",
        ]
        return "\n".join(lines)


def _percentile(samples: list[float], quantile: float) -> float:
    ordered = sorted(samples)
    return ordered[max(0, math.ceil(quantile * len(ordered)) - 1)]


async def _timed[T](call: Awaitable[T]) -> tuple[T, float]:
    started = time.perf_counter()
    result = await call
    return result, time.perf_counter() - started


class _Bench:
    def __init__(
        self,
        settings: Settings,
        sessions: Sessions,
        services: Services,
        runner: ExecuteRunner,
        owner: UUID,
    ) -> None:
        self._settings = settings
        self._services = services
        self._runner = runner
        self.sessions = sessions
        self.owner = owner
        self.chats: list[UUID] = []
        self.executions: list[UUID] = []
        """Runs without a chat; runs of a chat go with it."""

    async def chat(self) -> UUID:
        chat = Chat(
            id=uuid.uuid7(),
            created_by=self.owner,
            title="xavier bench",
            status=ChatStatus.READY,
            model=self._settings.agent.default_model,
            reasoning="off",
            mode=self._settings.chat.default_mode,
            tool_groups=["compute", "files"],
            execute_network=False,
            budget_usd=0.0,
            max_steps=self._settings.turn.max_steps,
        )
        async with self.sessions.begin() as session:
            session.add(chat)
        self.chats.append(chat.id)
        return chat.id

    async def code_run(self, chat_id: UUID | None) -> float:
        """Run ``print(1)`` the way `run_execution` does; return its seconds."""
        execution = Execution(
            id=uuid.uuid7(),
            created_by=self.owner,
            chat_id=chat_id,
            status=ExecutionStatus.PENDING,
            language="python",
            code="print(1)",
            network=False,
            export=[],
            input_artifact_ids=[],
            output_artifact_ids=[],
            metrics={},
        )
        async with self.sessions.begin() as session:
            session.add(execution)
        if chat_id is None:
            self.executions.append(execution.id)
        status, seconds = await _timed(self._runner.run(execution.id, lambda: None))
        if status is not ExecutionStatus.SUCCEEDED:
            raise RuntimeError(f"code_run ended {status.value}")
        return seconds

    async def call[A: ToolArgs](self, tool: Tool[A], args: A, chat_id: UUID) -> ToolResult:
        ctx = ToolContext(
            chat_id=chat_id,
            owner=self.owner,
            action_id=uuid.uuid7(),
            mode=self._settings.chat.default_mode,
            execute_network=False,
            image_input=True,
            services=self._services,
            risk=Risk.WRITE,
        )
        return await tool.run(tool.prepare(args), ctx)

    async def browse(self, chat_id: UUID, *actions: BrowserAction) -> dict[str, JsonValue]:
        result = await self.call(Browser(), BrowserArgs(actions=list(actions)), chat_id)
        for step in _list(result.data["results"]):
            if isinstance(step, dict) and not step["ok"]:
                raise RuntimeError(f"browser step failed: {step}")
        snapshot = result.data["snapshot"]
        if not isinstance(snapshot, dict):
            raise TypeError("browser returns a snapshot")
        return snapshot

    async def samples(
        self, report: Report, name: str, action: Callable[[], Awaitable[object]]
    ) -> None:
        report.actions[name] = [(await _timed(action()))[1] for _ in range(report.iterations)]


def _list(value: JsonValue) -> list[JsonValue]:
    return value if isinstance(value, list) else []


def _link(snapshot: dict[str, JsonValue]) -> str:
    """Return the id of the first link of the rendered snapshot."""
    for line in str(snapshot["elements"]).splitlines():
        element_id, role, *_rest = line.split(" ")
        if role == "link":
            return element_id
    raise RuntimeError("the page has no link")


async def _wait_ready(pool: ComputerPool) -> None:
    async with asyncio.timeout(_POOL_WAIT_SECONDS):
        while not pool.ready_count:  # noqa: ASYNC110 - the pool has no ready signal
            await asyncio.sleep(0.1)


async def _measure(
    bench: _Bench, workspaces: WorkspaceService, pool: ComputerPool, iterations: int
) -> Report:
    report = Report(iterations=iterations)
    refill = asyncio.create_task(pool.run())
    _none, fill = await _timed(_wait_ready(pool))
    report.starts.append(("Pool refill: new volumes and a Computer with Chromium", fill))
    main = await bench.chat()
    _active, warm = await _timed(workspaces.activate(main, computer=True))
    report.starts.append(("Activation with a pool Computer", warm))
    _page, first = await _timed(bench.browse(main, BrowserAction(action="navigate", url=PAGE)))
    report.starts.append(("First action after activation: navigate with snapshot", first))
    report.starts.append(("Latency to the first action", warm + first))
    refill.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await refill

    cold_chat = await bench.chat()
    _active, cold = await _timed(workspaces.activate(cold_chat, computer=True))
    report.starts.append(("Cold start without a pool Computer", cold))
    await workspaces.delete(cold_chat)

    await _actions(bench, report, main)
    await _scenario(bench, report, main)
    await _execute(bench, workspaces, report, main)

    _hibernated, hibernate = await _timed(workspaces.hibernate(main))
    report.starts.append(("Hibernation: Chromium stop and checkpoint", hibernate))
    restored, wake = await _timed(bench.browse(main))
    report.starts.append(("Wake from the checkpoint with a snapshot", wake))
    if restored["url"] == "about:blank":
        raise RuntimeError("the browser session was not restored from the checkpoint")
    return report


async def _actions(bench: _Bench, report: Report, chat_id: UUID) -> None:
    screen, run = Screen(), bench.call

    await bench.samples(report, "browser: snapshot", lambda: bench.browse(chat_id))
    await bench.samples(
        report,
        "browser: navigate",
        lambda: bench.browse(chat_id, BrowserAction(action="navigate", url=PAGE)),
    )
    await bench.samples(
        report,
        "browser: scroll",
        lambda: bench.browse(chat_id, BrowserAction(action="scroll", amount=200)),
    )
    await bench.samples(
        report,
        "browser: press End",
        lambda: bench.browse(chat_id, BrowserAction(action="press", key="End")),
    )
    await bench.samples(
        report,
        "browser: extract",
        lambda: bench.browse(chat_id, BrowserAction(action="extract", max_chars=2000)),
    )
    await bench.samples(
        report, "screen: screenshot", lambda: run(screen, ScreenArgs(action="screenshot"), chat_id)
    )
    await bench.samples(
        report,
        "screen: screenshot with marks",
        lambda: run(screen, ScreenArgs(action="screenshot", marks=True), chat_id),
    )
    await bench.samples(
        report,
        "screen: move",
        lambda: run(screen, ScreenArgs(action="move", x=640, y=700), chat_id),
    )
    await bench.samples(
        report,
        "screen: click",
        lambda: run(screen, ScreenArgs(action="click", x=640, y=780), chat_id),
    )
    await bench.samples(
        report, "screen: key", lambda: run(screen, ScreenArgs(action="key", keys="Home"), chat_id)
    )
    await bench.samples(
        report,
        "terminal: true",
        lambda: run(Terminal(), TerminalArgs(command="true", wait_s=10), chat_id),
    )


async def _execute(
    bench: _Bench, workspaces: WorkspaceService, report: Report, chat_id: UUID
) -> None:
    """Time `code_run` without and with a workspace, and workspace activation from a checkpoint."""
    report.execute["code_run without a workspace"] = [
        await bench.code_run(None) for _ in range(report.iterations)
    ]
    report.execute["code_run in the active workspace"] = [
        await bench.code_run(chat_id) for _ in range(report.iterations)
    ]
    plain = await bench.chat()
    await workspaces.activate(plain, computer=False)
    wakes: list[float] = []
    for _ in range(_ACTIVATION_SAMPLES):
        await workspaces.hibernate(plain)
        wakes.append((await _timed(workspaces.activate(plain, computer=False)))[1])
    report.execute["Workspace activation from a checkpoint, no Computer"] = wakes


async def _agent(sessions: Sessions) -> list[tuple[str, int, int, int, float]]:
    """Return the figures of the reference chats."""
    turns = (
        select(Turn.chat_id, func.count().label("turns"), func.sum(Turn.steps).label("steps"))
        .group_by(Turn.chat_id)
        .subquery()
    )
    calls = select(Action.chat_id, func.count().label("calls")).group_by(Action.chat_id).subquery()
    query = (
        select(
            Chat.title,
            func.coalesce(turns.c.turns, 0),
            func.coalesce(turns.c.steps, 0),
            func.coalesce(calls.c.calls, 0),
            Chat.spent_usd,
        )
        .outerjoin(turns, turns.c.chat_id == Chat.id)
        .outerjoin(calls, calls.c.chat_id == Chat.id)
        .where(Chat.title.startswith(REFERENCE_PREFIX))
        .order_by(Chat.id)
    )
    async with sessions() as session:
        rows = (await session.execute(query)).all()
    return [(title, turns, steps, calls, cost) for title, turns, steps, calls, cost in rows]


async def _scenario(bench: _Bench, report: Report, chat_id: UUID) -> None:
    """Open a page, follow its link and come back, as an agent would."""
    page, seconds = await _timed(bench.browse(chat_id, BrowserAction(action="navigate", url=PAGE)))
    report.steps.append(("navigate", seconds))
    link = _link(page)
    steps: list[tuple[str, Callable[[], Awaitable[object]]]] = [
        ("extract", lambda: bench.browse(chat_id, BrowserAction(action="extract"))),
        ("click the link", lambda: bench.browse(chat_id, BrowserAction(action="click", id=link))),
        ("scroll", lambda: bench.browse(chat_id, BrowserAction(action="scroll"))),
        ("back", lambda: bench.browse(chat_id, BrowserAction(action="back"))),
        (
            "screenshot with marks",
            lambda: bench.call(Screen(), ScreenArgs(action="screenshot", marks=True), chat_id),
        ),
        (
            "batch: new tab, navigate, close tab",
            lambda: bench.browse(
                chat_id,
                BrowserAction(action="tab_new", url=PAGE),
                BrowserAction(action="tab_close", tab=1),
            ),
        ),
        (
            "terminal: list downloads",
            lambda: bench.call(
                Terminal(), TerminalArgs(command="ls -la /workspace/downloads", wait_s=10), chat_id
            ),
        ),
        ("snapshot", lambda: bench.browse(chat_id)),
    ]
    for name, step in steps:
        _result, seconds = await _timed(step())
        report.steps.append((name, seconds))


@contextlib.asynccontextmanager
async def _stack(
    settings: Settings,
) -> AsyncGenerator[tuple[_Bench, WorkspaceService, ComputerPool]]:
    engine = create_engine(settings.database)
    docker_client = aiodocker.Docker()
    docker = DockerHost(docker_client, settings.worker.id)
    try:
        async with open_s3_client(settings.s3) as s3:
            sessions = create_sessions(engine)
            blobs = S3Adapter(s3, settings.s3.artifacts_bucket)
            artifacts = Artifacts(sessions, blobs)
            leases = Leases(sessions)
            computers = DockerComputerBackend(docker, settings.computer)
            pool = ComputerPool(computers, docker, 1)
            workspaces = WorkspaceService(
                settings, sessions, docker, s3, artifacts, pool=pool, leases=leases
            )
            client = await connect_temporal(settings, s3)
            executions = Executions(settings, sessions, client)
            runner = ExecuteRunner(
                settings,
                executions,
                DockerSandboxBackend(docker, settings.execute),
                artifacts,
                workspaces,
            )
            services = Services(
                settings=settings,
                sessions=sessions,
                credentials=AgentCredentials(
                    sessions,
                    Vault(settings.observe.credentials_key.get_secret_value()),
                    Redactor(settings.secret_values()),
                ),
                artifacts=artifacts,
                fetcher=HttpFetcher(),
                renderer=BrowserRenderer(computers, docker),
                sources=Sources(sessions, blobs),
                workspaces=workspaces,
                executions=executions,
                compute=ComputeService(leases),
                host_queue=host_queue(settings.temporal.task_queue, settings.worker.id),
            )
            owner = User(
                id=uuid.uuid7(),
                username=f"bench-{uuid.uuid7().hex[-12:]}",
                name="xavier bench",
                password_hash="!",  # noqa: S106 - no password signs in as this user
            )
            async with sessions.begin() as session:
                session.add(owner)
            bench = _Bench(settings, sessions, services, runner, owner.id)
            try:
                yield bench, workspaces, pool
            finally:
                for chat_id in bench.chats:
                    await workspaces.delete(chat_id)
                async with sessions.begin() as session:
                    for chat_id in bench.chats:
                        await session.delete(await session.get_one(Chat, chat_id))
                    for execution_id in bench.executions:
                        await session.delete(await session.get_one(Execution, execution_id))
                    await session.delete(await session.get_one(User, owner.id))
                containers, volumes = await docker.owned()
                for container_id, _labels in containers:
                    await docker.remove_container(container_id)
                for name, _labels in volumes:
                    await docker.remove_volume(name)
    finally:
        await docker_client.close()
        await engine.dispose()


async def run_bench(settings: Settings, iterations: int) -> str:
    """Measure Compute and Execute, summarize the reference chats and return the report.

    Runs as its own worker id, so the containers and volumes it creates are its own and
    removed at the end, and a running worker does not treat them as orphans of its own.
    """
    worker = settings.worker.model_copy(update={"id": f"bench-{uuid.uuid7().hex[:12]}"})
    settings = settings.model_copy(update={"worker": worker})
    async with _stack(settings) as (bench, workspaces, pool):
        report = await _measure(bench, workspaces, pool, iterations)
        report.agent = await _agent(bench.sessions)
    return report.markdown()
