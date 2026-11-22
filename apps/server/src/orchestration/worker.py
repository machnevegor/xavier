"""The ``worker`` process: Temporal workers, MCP gateway, internal server and heartbeat."""

import asyncio
import contextlib
import logging
from collections.abc import AsyncGenerator
from datetime import timedelta

import aiodocker
import uvicorn
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert
from starlette.types import ASGIApp
from temporalio.worker import Worker

from src.agent.gateway import Actions, Executor, Gateway
from src.agent.profile import scrub_environment
from src.agent.questions import Questions
from src.agent.runner import Runner
from src.compute.docker_backend import DockerComputerBackend
from src.compute.pool import ComputerPool
from src.compute.service import ComputeService
from src.config import Settings
from src.credentials.service import AgentCredentials
from src.db.engine import Sessions, create_engine, create_sessions
from src.db.models import Worker as WorkerRow
from src.docker.client import DockerHost
from src.docker.firewall_probe import check_firewall
from src.events import ACTIONS_CHANNEL, MESSAGES_CHANNEL, QUESTIONS_CHANNEL, Listener
from src.execute.docker_backend import DockerSandboxBackend
from src.execute.service import ExecuteRunner, Executions
from src.leases import Leases
from src.observe.credentials import Credentials, Vault
from src.observe.fetch import BrowserRenderer, HttpFetcher
from src.observe.service import Observe, Sources
from src.orchestration.activities import (
    ChatActivities,
    MaintenanceActivities,
    ObserveActivities,
    ScheduleActivities,
    WorkspaceActivities,
)
from src.orchestration.client import connect_temporal
from src.orchestration.models import host_queue
from src.orchestration.schedules import SourceSchedules, ensure_maintenance_schedule
from src.orchestration.workflows import (
    ChatScheduleWorkflow,
    ChatWorkflow,
    ExecutionWorkflow,
    MaintenanceWorkflow,
    ObservePollWorkflow,
)
from src.policy.redact import Redactor
from src.storage.artifacts import Artifacts
from src.storage.blobs import S3Adapter, open_s3_client
from src.tools.base import Services
from src.tools.registry import default_registry
from src.worker_http import internal_app
from src.workspace.service import WorkspaceService

logger = logging.getLogger(__name__)

WORKER_HEARTBEAT_SECONDS = 10.0
HEARTBEAT_THROTTLE = timedelta(seconds=2)
"""Longest delay of an activity heartbeat, and so of a cancellation reaching a running turn."""


@contextlib.asynccontextmanager
async def serve(app: ASGIApp, host: str, port: int) -> AsyncGenerator[int]:
    """Serve ``app`` and yield the port it listens on."""
    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_config=None,
        access_log=False,
        lifespan="on",
        server_header=False,
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    while not server.started:
        if task.done():
            task.result()
            raise RuntimeError("the gateway server stopped during startup")
        await asyncio.sleep(0.05)
    try:
        yield server.servers[0].sockets[0].getsockname()[1]
    finally:
        server.should_exit = True
        await task


async def _heartbeat(sessions: Sessions, settings: Settings) -> None:
    url = settings.worker.internal_url()
    while True:
        async with sessions.begin() as session:
            await session.execute(
                insert(WorkerRow)
                .values(id=settings.worker.id, internal_url=url, heartbeat_at=func.now())
                .on_conflict_do_update(
                    index_elements=[WorkerRow.id],
                    set_={"internal_url": url, "heartbeat_at": func.now()},
                )
            )
        await asyncio.sleep(WORKER_HEARTBEAT_SECONDS)


async def run_worker(settings: Settings) -> None:  # noqa: PLR0915 - the composition root
    """Run the worker until cancelled.

    The worker refuses to start when runs with network can reach internal addresses.
    """
    scrub_environment()
    settings.agent.cwd.mkdir(parents=True, exist_ok=True)
    engine = create_engine(settings.database)
    sessions = create_sessions(engine)
    listeners = {
        channel: Listener(settings.database.dsn(), channel)
        for channel in (ACTIONS_CHANNEL, MESSAGES_CHANNEL, QUESTIONS_CHANNEL)
    }
    fetcher = HttpFetcher()
    task_queue = settings.temporal.task_queue
    own_queue = host_queue(task_queue, settings.worker.id)
    async with contextlib.AsyncExitStack() as stack:
        stack.push_async_callback(engine.dispose)
        docker_client = aiodocker.Docker()
        stack.push_async_callback(docker_client.close)
        docker = DockerHost(docker_client, settings.worker.id)
        await docker.ensure_network()
        await check_firewall(docker, settings, await docker.network_gateway())
        s3 = await stack.enter_async_context(open_s3_client(settings.s3))
        blobs = S3Adapter(s3, settings.s3.artifacts_bucket)
        artifacts = Artifacts(sessions, blobs)
        for listener in listeners.values():
            await listener.start()
            stack.push_async_callback(listener.close)
        stack.push_async_callback(fetcher.close)
        client = await connect_temporal(settings, s3)
        leases = Leases(sessions)
        computers = DockerComputerBackend(docker, settings.computer)
        pool = ComputerPool(computers, docker, settings.computer.pool_size)
        renderer = BrowserRenderer(computers, docker)
        vault = Vault(settings.observe.credentials_key.get_secret_value())
        observe = Observe(
            settings=settings.observe,
            sessions=sessions,
            blobs=blobs,
            fetcher=fetcher,
            renderer=renderer,
            credentials=Credentials(sessions, vault, fetcher),
            vault=vault,
        )
        workspaces = WorkspaceService(
            settings, sessions, docker, s3, artifacts, pool=pool, leases=leases
        )
        await workspaces.cleanup_orphans()
        executions = Executions(settings, sessions, client)
        redactor = Redactor(settings.secret_values())
        credentials = AgentCredentials(sessions, vault, redactor)
        execute_runner = ExecuteRunner(
            settings,
            executions,
            DockerSandboxBackend(docker, settings.execute),
            artifacts,
            workspaces,
            environment=credentials,
        )
        executor = Executor(Actions(sessions), artifacts, redactor)
        gateway = Gateway(
            default_registry(),
            executor,
            Services(
                settings=settings,
                sessions=sessions,
                credentials=credentials,
                artifacts=artifacts,
                fetcher=fetcher,
                renderer=renderer,
                sources=Sources(sessions, blobs),
                workspaces=workspaces,
                executions=executions,
                compute=ComputeService(leases),
                host_queue=own_queue,
            ),
            listeners[ACTIONS_CHANNEL],
            settings,
        )
        port = await stack.enter_async_context(serve(gateway.app(), "127.0.0.1", 0))
        gateway.base_url = f"http://127.0.0.1:{port}"
        await stack.enter_async_context(
            serve(
                internal_app(settings, workspaces, leases),
                settings.worker.internal_host,
                settings.worker.internal_port,
            )
        )
        runner = Runner(
            settings,
            sessions,
            gateway,
            gateway.services,
            blobs,
            inputs=listeners[MESSAGES_CHANNEL],
            questions=Questions(sessions, listeners[QUESTIONS_CHANNEL]),
            redactor=redactor,
        )
        activities = [
            *ChatActivities(settings, sessions, runner, gateway, blobs).all(),
            *WorkspaceActivities(
                sessions, workspaces, executions, execute_runner, task_queue
            ).all(),
            *MaintenanceActivities(settings, sessions, blobs, client).all(),
            *ObserveActivities(
                sessions, observe, SourceSchedules(client, task_queue), task_queue
            ).all(),
            *ScheduleActivities(sessions, client, task_queue).all(),
        ]
        shared = Worker(
            client,
            task_queue=task_queue,
            workflows=[
                ChatWorkflow,
                ExecutionWorkflow,
                ObservePollWorkflow,
                ChatScheduleWorkflow,
                MaintenanceWorkflow,
            ],
            activities=activities,
            max_concurrent_activities=settings.worker.max_concurrent_activities,
            max_heartbeat_throttle_interval=HEARTBEAT_THROTTLE,
        )
        host = Worker(
            client,
            task_queue=own_queue,
            activities=activities,
            max_concurrent_activities=settings.worker.max_concurrent_activities,
            max_heartbeat_throttle_interval=HEARTBEAT_THROTTLE,
        )
        await ensure_maintenance_schedule(client, task_queue)
        logger.info("worker %s started; gateway at %s", settings.worker.id, gateway.base_url)
        async with asyncio.TaskGroup() as group:
            group.create_task(_heartbeat(sessions, settings))
            group.create_task(pool.run())
            group.create_task(shared.run())
            group.create_task(host.run())
