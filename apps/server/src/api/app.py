"""FastAPI application."""

import contextlib
from collections.abc import AsyncGenerator
from typing import Literal

import httpx2
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi_swagger import patch_fastapi
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from temporalio.service import RPCError

from src.api.actions import router as actions
from src.api.alerts import router as alerts
from src.api.api_keys import router as api_keys
from src.api.approvals import router as approvals
from src.api.artifacts import router as artifacts
from src.api.auth import router as auth
from src.api.catalog import router as catalog
from src.api.chats import router as chats
from src.api.common import UNAVAILABLE, ApiRoute, install, populate_openapi_tags
from src.api.computer import router as computer
from src.api.credentials import router as credentials
from src.api.csp import ContentSecurityPolicy
from src.api.dependencies import Services, ServicesDep
from src.api.events import router as events
from src.api.executions import router as executions
from src.api.links import router as links
from src.api.media import router as media
from src.api.messages import router as messages
from src.api.metrics import router as metrics
from src.api.policy import router as policy
from src.api.questions import router as questions
from src.api.schedules import router as schedules
from src.api.sessions import router as sessions
from src.api.sources import router as sources
from src.api.subscriptions import router as subscriptions
from src.api.turns import router as turns
from src.api.wakeups import router as wakeups
from src.api.workspace import router as workspace
from src.config import Settings
from src.db.engine import create_engine, create_sessions
from src.errors import UnavailableError
from src.events import EVENTS_CHANNEL, Listener
from src.execute.service import Executions
from src.leases import Leases
from src.observe.credentials import Vault
from src.observe.fetch import HttpFetcher
from src.observe.service import Sources
from src.orchestration.client import ChatCommands, connect_temporal
from src.orchestration.schedules import ChatSchedules, SourceSchedules
from src.storage.artifacts import Artifacts
from src.storage.blobs import S3Adapter, open_s3_client

WORKER_TIMEOUT = 30.0
"""Seconds ``api`` waits for a worker internal server."""
PREFIX = "/api/v1"
"""Every route, the documentation and the health checks included, lives under it."""
DOCS_URL = f"{PREFIX}/docs"
RESOURCES = (
    auth,
    sessions,
    api_keys,
    chats,
    messages,
    events,
    turns,
    actions,
    wakeups,
    questions,
    approvals,
    links,
    media,
    artifacts,
    workspace,
    computer,
    credentials,
    policy,
    catalog,
    schedules,
    sources,
    subscriptions,
    alerts,
    executions,
    metrics,
)
"""Router modules of the resources; each module docstring documents its tag."""


class Health(BaseModel):
    """Liveness of the process."""

    status: Literal["ok"] = "ok"


def _operation_id(route: APIRoute) -> str:
    """Name each operation after its handler."""
    name = getattr(route.endpoint, "__name__", None)
    if not isinstance(name, str) or name == "":
        raise RuntimeError("an operation has no handler name")
    return name


def create_app(settings: Settings) -> FastAPI:
    """Build the API application; connections open in the application lifespan."""

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        async with contextlib.AsyncExitStack() as stack:
            engine = create_engine(settings.database)
            stack.push_async_callback(engine.dispose)
            sessions = create_sessions(engine)
            s3 = await stack.enter_async_context(open_s3_client(settings.s3))
            listener = Listener(settings.database.dsn(), EVENTS_CHANNEL)
            await listener.start()
            stack.push_async_callback(listener.close)
            client = await connect_temporal(settings, s3)
            workers = httpx2.AsyncClient(timeout=WORKER_TIMEOUT)
            stack.push_async_callback(workers.aclose)
            fetcher = HttpFetcher()
            stack.push_async_callback(fetcher.close)
            blobs = S3Adapter(s3, settings.s3.artifacts_bucket)
            task_queue = settings.temporal.task_queue
            app.state.services = Services(
                settings=settings,
                sessions=sessions,
                temporal=client,
                artifacts=Artifacts(sessions, blobs),
                commands=ChatCommands(client, task_queue),
                executions=Executions(settings, sessions, client),
                events=listener,
                workers=workers,
                leases=Leases(sessions),
                blobs=blobs,
                fetcher=fetcher,
                sources=Sources(sessions, blobs),
                vault=Vault(settings.observe.credentials_key.get_secret_value()),
                source_schedules=SourceSchedules(client, task_queue),
                chat_schedules=ChatSchedules(client, task_queue),
            )
            yield

    app = FastAPI(
        title="Xavier",
        generate_unique_id_function=_operation_id,
        openapi_url=f"{PREFIX}/openapi.json",
        docs_url=None,
        redoc_url=None,
        swagger_ui_oauth2_redirect_url=None,
        lifespan=lifespan,
    )
    app.router.route_class = ApiRoute
    app.state.settings = settings
    install(app)
    patch_fastapi(
        app,
        docs_url=DOCS_URL,
        redirect_from_root_to_docs=False,
        swagger_js_url=f"{DOCS_URL}/swagger-ui-bundle.js",
        swagger_css_url=f"{DOCS_URL}/swagger-ui.css",
        swagger_favicon_url=f"{DOCS_URL}/favicon-32x32.png",
    )
    app.add_middleware(ContentSecurityPolicy, docs_url=DOCS_URL)
    FastAPIInstrumentor.instrument_app(app, excluded_urls="healthz,readyz")

    @app.get(f"{PREFIX}/healthz", tags=["health"])
    async def healthz() -> Health:
        """Report that the process is running. Does not check dependencies."""
        return Health()

    @app.get(f"{PREFIX}/readyz", tags=["health"], responses={503: UNAVAILABLE})
    async def readyz(services: ServicesDep) -> Health:
        """Report that the process can serve requests: PostgreSQL and Temporal answer."""
        try:
            async with services.sessions() as session:
                await session.execute(select(1))
            serving = await services.temporal.service_client.check_health()
        except (SQLAlchemyError, OSError, RPCError) as error:
            raise UnavailableError from error
        if not serving:
            raise UnavailableError
        return Health()

    for module in RESOURCES:
        app.include_router(module.router, prefix=PREFIX)
        populate_openapi_tags(app, module)
    return app
