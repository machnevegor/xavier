"""Tool contract."""

import asyncio
import contextlib
from abc import ABC, abstractmethod
from collections.abc import Awaitable
from dataclasses import dataclass, field
from typing import ClassVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, JsonValue

from src.compute.channel import ComputerError
from src.compute.service import ComputeService
from src.config import Settings
from src.credentials.service import AgentCredentials
from src.db.engine import Sessions
from src.db.models import ToolGroup
from src.execute.service import Executions
from src.observe.fetch import BrowserRenderer, HttpFetcher
from src.observe.service import Sources
from src.policy.model import Mode, Resource, Risk
from src.storage.artifacts import Artifacts
from src.workspace.service import Active, WorkspaceError, WorkspaceService


class ToolArgs(BaseModel):
    """Base of tool argument schemas: unknown arguments are rejected."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class ToolError(Exception):
    """A tool failure with a stable code the model can act on.

    Codes: ``invalid_args``, ``denied``, ``not_found``, ``timeout``, ``env_busy``, ``host_full``,
    ``workspace_full``, ``needs_capability``, ``turn_stopping``, ``turn_suspending``,
    ``turn_interrupted``, ``failed``.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class Image:
    """An image for models with image input."""

    mime_type: str
    data: str
    """Base64 content."""


@dataclass(slots=True)
class ToolResult:
    """Handler output. ``data`` is compact JSON for the model."""

    data: dict[str, JsonValue]
    images: list[Image] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class Services:
    """What handlers may use."""

    settings: Settings
    sessions: Sessions
    artifacts: Artifacts
    fetcher: HttpFetcher
    renderer: BrowserRenderer
    sources: Sources
    workspaces: WorkspaceService
    executions: Executions
    compute: ComputeService
    credentials: AgentCredentials
    host_queue: str
    """Task queue bound to this worker, where chat runs execute next to the workspace."""


@dataclass(frozen=True, slots=True)
class ToolContext:
    """The chat and action a handler runs for."""

    chat_id: UUID
    owner: UUID
    """The chat's owner."""
    action_id: UUID
    mode: Mode
    execute_network: bool
    image_input: bool
    """Whether the chat model accepts images."""
    services: Services
    risk: Risk | None = None
    """Risk the call was recorded and approved with; set when the handler runs."""
    turn_id: UUID | None = None
    interrupted: asyncio.Event | None = None
    """Set when new operator input interrupts the turn: waits for jobs return early."""
    proactive: bool = False
    declined: tuple[str, ...] = ()
    """Capabilities the operator declined for the chat."""

    async def wait_or_interrupt[T](self, waiting: Awaitable[T], within: float) -> T | None:
        """Return the result of ``waiting`` within ``within`` seconds.

        ``None`` means the time ran out or the turn was interrupted; ``waiting`` is cancelled.
        """
        task = asyncio.ensure_future(waiting)
        if self.interrupted is None:
            await asyncio.wait({task}, timeout=within)
        else:
            interrupted = asyncio.create_task(self.interrupted.wait())
            try:
                await asyncio.wait(
                    {task, interrupted}, timeout=within, return_when=asyncio.FIRST_COMPLETED
                )
            finally:
                interrupted.cancel()
        if task.done():
            return task.result()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        return None


class Tool[A: ToolArgs](ABC):
    """A tool the agent calls through the MCP gateway; its identity attributes are class-level."""

    name: str
    groups: frozenset[ToolGroup]
    """Groups any of which enables the tool."""
    description: str
    """Description for the model."""
    consequences: str
    """What happens when the call runs, for the approval card."""
    risk: Risk
    """Base risk; `classify` may raise it for particular arguments."""
    interrupt_safe: ClassVar[bool] = False
    """Interrupting the handler leaves no side effects, so it ends as ``cancelled``."""
    retryable: ClassVar[bool] = False
    """The handler may run again after an executor is lost."""
    always_ask: ClassVar[bool] = False
    """Every call waits for the operator, whatever the mode and the rules."""
    suspends: ClassVar[bool] = False
    """A call waiting for approval suspends the turn at once: approving it changes what the
    chat may do, which a new turn picks up."""
    args_type: type[A]

    async def classify(self, args: A, ctx: ToolContext) -> Risk:
        """Return the risk of this call."""
        del args, ctx
        return self.risk

    async def refuse(self, args: A, ctx: ToolContext) -> str | None:
        """Return why the call is denied before moderation, or ``None``."""
        del args, ctx
        return None

    def resources(self, args: A) -> list[Resource]:
        """Return the hosts, URLs and paths the call touches, for policy rules."""
        del args
        return []

    def prepare(self, args: A) -> A:
        """Return normalized arguments; policy is evaluated on them and they are executed.

        Raises
        ------
        src.policy.urls.ResourceRejectedError
            If a resource is outside what tools may touch.
        """
        return args

    def redact(self, arguments: dict[str, JsonValue], ctx: ToolContext) -> dict[str, JsonValue]:
        """Return the arguments as shown in the timeline and approval card, without secrets."""
        del ctx
        return arguments

    @abstractmethod
    async def run(self, args: A, ctx: ToolContext) -> ToolResult:
        """Execute the call.

        Raises
        ------
        ToolError
            On a failure the model should see with its code.
        """


async def use_workspace(
    ctx: ToolContext, *, computer: bool = False
) -> tuple[Active, dict[str, JsonValue]]:
    """Activate the chat workspace on this worker, with its Computer on request, and record the use.

    Returns the workspace and the result fields to add: ``workspace_restored`` with the
    checkpoint time when this call restored it, and ``computer_restarted`` with the reason when
    it replaced a Computer that had stopped.

    Raises
    ------
    ToolError
        ``env_busy``, ``host_full`` or ``failed`` when it cannot be activated.
    """
    workspaces = ctx.services.workspaces
    try:
        active = await workspaces.activate(ctx.chat_id, computer=computer)
    except WorkspaceError as error:
        raise ToolError(error.code, error.message) from error
    except ComputerError as error:
        raise ToolError("failed", f"the Computer did not start: {error.message}") from error
    await workspaces.touch(ctx.chat_id)
    return active, workspace_changes(active)


def workspace_changes(active: Active) -> dict[str, JsonValue]:
    """Return the result fields that report what an activation changed."""
    extra: dict[str, JsonValue] = {}
    if active.restored_at is not None:
        extra["workspace_restored"] = {"checkpoint_at": active.restored_at.isoformat()}
    if active.computer_failure is not None:
        extra["computer_restarted"] = {
            "reason": f"the Computer stopped because {active.computer_failure}; "
            "its processes and browser state were lost, /workspace and the home directory kept"
        }
    return extra
