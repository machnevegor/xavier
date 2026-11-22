"""MCP gateway: tool contracts, moderation, approvals and execution for agent turns.

Harness reaches the gateway at ``/mcp/<turn_secret>``. The secret identifies one registered turn
attempt; the chat, tool groups and mode come from the registration, never from the model.
"""

import asyncio
import contextlib
import dataclasses
import hashlib
import json
import logging
import secrets
import uuid
from collections.abc import Awaitable, Collection, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

import mcp_types as mt
from mcp.server import Server, ServerRequestContext
from mcp.shared.exceptions import MCPError
from opentelemetry import context as otel_context
from opentelemetry.context import Context
from pydantic import JsonValue, ValidationError
from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.applications import Starlette
from starlette.requests import Request

from src.agent import capabilities
from src.config import Settings
from src.credentials.service import REQUEST_TOOL as CREDENTIAL_REQUEST
from src.db.engine import Sessions
from src.db.models import (
    DECISION_STATUS,
    FINAL_ACTION_STATUSES,
    PENDING_ACTION_STATUSES,
    Action,
    ActionStatus,
    Chat,
    Credential,
    Decision,
    PolicyRule,
    ToolGroup,
    Turn,
)
from src.errors import InvalidRequestError, NotFoundError
from src.events import ACTIONS_CHANNEL, Listener, NewEvent, append_events, notify
from src.policy.engine import decide, grant_patterns
from src.policy.model import Breadth, Effect, Mode, Resource, ResourceKind, Risk, Rule, Scope
from src.policy.redact import Redactor
from src.policy.urls import ResourceRejectedError
from src.storage.artifacts import Artifacts
from src.telemetry import TOOL_CALLS, tracer
from src.tools.base import Image, Services, ToolArgs, ToolContext, ToolError, ToolResult
from src.tools.registry import AnyTool, Registry

logger = logging.getLogger(__name__)

HANDLER_DEADLINE = 280.0
"""Upper bound of one handler; below the Harness MCP call timeout of 300 s."""
LARGE_RESULT_BYTES = 32 * 1024
PREVIEW_CHARS = 4000
FEED_PREVIEW_CHARS = 2000
SUPERSEDED = "superseded"
"""Error code of an undecided action that new operator input or a revision replaced."""

_RECHECK_SECONDS = 5.0
_STATUS_ON_EFFECT = {
    Effect.ALLOW: ActionStatus.RUNNING,
    Effect.ASK: ActionStatus.AWAITING_APPROVAL,
    Effect.DENY: ActionStatus.DENIED,
}


def canonical_json(value: JsonValue) -> str:
    """Return the canonical JSON text used for argument hashes."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def args_hash(arguments: JsonValue) -> str:
    """Return the SHA-256 of the canonical arguments."""
    return hashlib.sha256(canonical_json(arguments).encode()).hexdigest()


class StaleAttemptError(Exception):
    """A newer attempt owns the turn."""


class Actions:
    """Action rows: creation, CAS transitions, ``NOTIFY xv_action`` and feed events."""

    def __init__(self, sessions: Sessions) -> None:
        self._sessions = sessions

    async def rules(self, chat_id: UUID, owner: UUID) -> list[Rule]:
        """Return the owner's global rules and the rules of the chat."""
        async with self._sessions() as session:
            rows = await session.scalars(
                select(PolicyRule).where(
                    PolicyRule.created_by == owner,
                    or_(PolicyRule.chat_id.is_(None), PolicyRule.chat_id == chat_id),
                )
            )
            return [
                Rule(r.id, r.tool_pattern, r.resource_pattern, r.chat_id, r.effect, r.priority)
                for r in rows
            ]

    async def create(self, action: Action, event: dict[str, JsonValue]) -> None:
        """Insert ``action`` if its turn attempt is current.

        Raises
        ------
        StaleAttemptError
            If a newer attempt owns the turn.
        """
        async with self._sessions.begin() as session:
            attempt = await session.scalar(
                select(Turn.attempt).where(Turn.id == action.turn_id).with_for_update(read=True)
            )
            if attempt != action.attempt:
                raise StaleAttemptError(str(action.turn_id))
            session.add(action)
            await session.flush()
            await append_events(
                session, action.chat_id, [NewEvent("action", event, action.turn_id)]
            )
            await notify(session, ACTIONS_CHANNEL, str(action.id))

    async def transition(
        self,
        action_id: UUID,
        expected: Collection[ActionStatus],
        new: ActionStatus,
        *,
        event: dict[str, JsonValue] | None = None,
        **values: object,
    ) -> bool:
        """Move the action from one of ``expected`` to ``new``; return whether this call did.

        Final statuses clear the exact pending arguments.
        """
        async with self._sessions.begin() as session:
            return await _move(session, action_id, expected, new, event=event, values=values)

    async def decide(
        self, action_id: UUID, decision: Decision, scope: Scope, breadth: Breadth
    ) -> tuple[Action, bool]:
        """Store an operator decision with what it grants.

        Returns the action and whether this call decided it. A ``chat`` or ``always`` approval
        adds allow rules for the action's resources at ``breadth``. A decision on a capability
        request enables or declines the capability.

        Raises
        ------
        NotFoundError
            If the action does not exist.
        InvalidRequestError
            If the scope or the breadth does not apply to the action.
        """
        async with self._sessions.begin() as session:
            action = await session.get(Action, action_id, with_for_update=True)
            if action is None:
                raise NotFoundError(f"action {action_id} not found")
            if action.status not in PENDING_ACTION_STATUSES:
                return action, False
            try:
                if scope is not Scope.ONCE:
                    await _add_rules(session, action, decision, scope, breadth)
                if action.tool == capabilities.REQUEST_TOOL and decision is not Decision.CANCEL:
                    await capabilities.settle_request(
                        session, action, approved=decision is Decision.APPROVE
                    )
            except ValueError as error:
                raise InvalidRequestError(str(error)) from error
            if (
                action.tool == CREDENTIAL_REQUEST
                and decision is Decision.APPROVE
                and await session.get(Credential, action_id) is None
            ):
                raise InvalidRequestError(
                    f"enter the credential at /credentials/requests/{action_id} to approve"
                )
            await _move(
                session,
                action_id,
                PENDING_ACTION_STATUSES,
                DECISION_STATUS[decision],
                event={"decision": decision.value, "scope": scope.value},
                values={"decision": decision, "decided_at": func.now()},
            )
        return await self.get(action_id), True

    async def supersede(self, action_ids: Iterable[UUID]) -> set[UUID]:
        """Cancel the undecided actions that new operator input replaced; return those it did."""
        return {
            action_id
            for action_id in action_ids
            if await self.transition(
                action_id,
                PENDING_ACTION_STATUSES,
                ActionStatus.CANCELLED,
                error_code=SUPERSEDED,
                error="new operator input arrived before the decision",
            )
        }

    async def get(self, action_id: UUID) -> Action:
        """Return the action.

        Raises
        ------
        LookupError
            If it does not exist.
        """
        async with self._sessions() as session:
            action = await session.get(Action, action_id)
        if action is None:
            raise LookupError(str(action_id))
        return action

    async def status(self, action_id: UUID) -> ActionStatus:
        """Return the current status."""
        return (await self.get(action_id)).status


async def _move(  # noqa: PLR0913 - the parts of one transition
    session: AsyncSession,
    action_id: UUID,
    expected: Collection[ActionStatus],
    new: ActionStatus,
    *,
    event: dict[str, JsonValue] | None,
    values: dict[str, object],
) -> bool:
    if new in FINAL_ACTION_STATUSES:
        values = {"pending_arguments": None, "finished_at": func.now(), **values}
    row = (
        await session.execute(
            update(Action)
            .where(Action.id == action_id, Action.status.in_(list(expected)))
            .values(status=new, **values)
            .returning(Action.chat_id, Action.turn_id, Action.tool)
        )
    ).one_or_none()
    if row is None:
        return False
    data: dict[str, JsonValue] = {
        "action_id": str(action_id),
        "tool": row.tool,
        "status": new.value,
        **(event or {}),
    }
    await append_events(session, row.chat_id, [NewEvent("action", data, row.turn_id)])
    await notify(session, ACTIONS_CHANNEL, str(action_id))
    return True


async def _add_rules(
    session: AsyncSession, action: Action, decision: Decision, scope: Scope, breadth: Breadth
) -> None:
    """Add the allow rules of a scoped approval that do not exist yet.

    Raises
    ------
    ValueError
        If the approval cannot have a scope.
    """
    if decision is not Decision.APPROVE:
        raise ValueError("only an approval has a scope")
    if action.rule_id is not None:
        raise ValueError("a rule asks for this action; change the rule in Guardrails")
    if action.tool in {capabilities.REQUEST_TOOL, CREDENTIAL_REQUEST}:
        raise ValueError("a request is decided once")
    stored = action.resources if isinstance(action.resources, list) else []
    resources = [
        Resource(ResourceKind(item["kind"]), str(item["value"]))
        for item in stored
        if isinstance(item, dict)
    ]
    chat_id = action.chat_id if scope is Scope.CHAT else None
    owner = await session.scalar(select(Chat.created_by).where(Chat.id == action.chat_id))
    if owner is None:
        raise ValueError(f"chat {action.chat_id} not found")
    for pattern in grant_patterns(resources, breadth):
        exists = await session.scalar(
            select(PolicyRule.id).where(
                PolicyRule.created_by == owner,
                PolicyRule.tool_pattern == action.tool,
                PolicyRule.resource_pattern.is_(None)
                if pattern is None
                else PolicyRule.resource_pattern == pattern,
                PolicyRule.chat_id.is_(None) if chat_id is None else PolicyRule.chat_id == chat_id,
                PolicyRule.effect == Effect.ALLOW,
            )
        )
        if exists is None:
            session.add(
                PolicyRule(
                    id=uuid.uuid7(),
                    created_by=owner,
                    tool_pattern=action.tool,
                    resource_pattern=pattern,
                    chat_id=chat_id,
                    effect=Effect.ALLOW,
                    description=f"Approved for {scope.value} from action {action.id}",
                )
            )


def tool_result(
    payload: dict[str, JsonValue], images: list[Image] | None = None, *, is_error: bool = False
) -> mt.CallToolResult:
    """Return an MCP result with ``payload`` as JSON text, then images."""
    content: list[mt.ContentBlock] = [mt.TextContent(type="text", text=canonical_json(payload))]
    content += [
        mt.ImageContent(type="image", data=image.data, mime_type=image.mime_type)
        for image in images or []
    ]
    return mt.CallToolResult(content=content, is_error=is_error)


def error_result(code: str, message: str, action_id: UUID | None = None) -> mt.CallToolResult:
    """Return a failed MCP result with a stable error code."""
    payload: dict[str, JsonValue] = {
        "status": "failed",
        "error": {"code": code, "message": message},
    }
    if action_id is not None:
        payload = {"action_id": str(action_id), **payload}
    return tool_result(payload, is_error=True)


class Executor:
    """Run a handler for an action in ``running`` and record its outcome."""

    def __init__(self, actions: Actions, artifacts: Artifacts, redactor: Redactor) -> None:
        self.actions = actions
        self.redact = redactor
        self._artifacts = artifacts

    async def execute(self, tool: AnyTool, args: ToolArgs, ctx: ToolContext) -> mt.CallToolResult:
        """Run the handler under the deadline; interruption ends ``cancelled`` or ``uncertain``."""
        status = "interrupted"
        attributes = {"xavier.tool": tool.name, "xavier.action_id": str(ctx.action_id)}
        with tracer.start_as_current_span("xavier.tool", attributes=attributes):
            try:
                result = await self._execute(tool, args, ctx)
                status = "failed" if result.is_error else "succeeded"
                return result
            finally:
                TOOL_CALLS.add(1, {"tool": tool.name, "status": status})

    async def _execute(self, tool: AnyTool, args: ToolArgs, ctx: ToolContext) -> mt.CallToolResult:
        action_id = ctx.action_id
        try:
            async with asyncio.timeout(HANDLER_DEADLINE):
                result = await tool.run(args, ctx)
        except asyncio.CancelledError:
            status = ActionStatus.CANCELLED if tool.interrupt_safe else ActionStatus.UNCERTAIN
            await self.actions.transition(
                action_id, {ActionStatus.RUNNING}, status, error_code="interrupted"
            )
            raise
        except ToolError as error:
            return await self._fail(action_id, error.code, error.message)
        except TimeoutError:
            return await self._fail(action_id, "timeout", "the tool did not finish in time")
        except Exception:
            logger.exception("tool %s failed", tool.name)
            return await self._fail(action_id, "failed", "internal tool error")
        return await self._succeed(action_id, ctx, result)

    async def _fail(self, action_id: UUID, code: str, message: str) -> mt.CallToolResult:
        message = self.redact(message)
        await self.actions.transition(
            action_id,
            {ActionStatus.RUNNING},
            ActionStatus.FAILED,
            error_code=code,
            error=message,
            event={"error": {"code": code, "message": message}},
        )
        return error_result(code, message, action_id)

    async def _succeed(
        self, action_id: UUID, ctx: ToolContext, result: ToolResult
    ) -> mt.CallToolResult:
        data = self.redact.json(result.data)
        if not isinstance(data, dict):
            raise TypeError("tool data must be a JSON object")
        payload: dict[str, JsonValue] = {"action_id": str(action_id), "status": "succeeded", **data}
        text = canonical_json(payload)
        if len(text.encode()) > LARGE_RESULT_BYTES:
            artifact = await self._artifacts.create(
                chat_id=ctx.chat_id,
                name=f"result-{action_id}.json",
                content_type="application/json",
                data=text.encode(),
                source="result",
            )
            payload = {
                "action_id": str(action_id),
                "status": "succeeded",
                "artifact_id": str(artifact.id),
                "size_bytes": artifact.size_bytes,
                "preview": text[:PREVIEW_CHARS],
                "truncated": True,
            }
        images = result.images if ctx.image_input else []
        if result.images and not ctx.image_input:
            payload["images_omitted"] = "the chat model does not accept images"
        stored = {**payload, "images": len(images)} if images else payload
        await self.actions.transition(
            action_id,
            {ActionStatus.RUNNING},
            ActionStatus.SUCCEEDED,
            result=stored,
            event={"result": canonical_json(stored)[:FEED_PREVIEW_CHARS]},
        )
        return tool_result(payload, images)


class TurnState(StrEnum):
    """Whether a registered turn accepts calls."""

    OPEN = "open"
    SUSPENDING = "suspending"
    INTERRUPTING = "interrupting"
    """New operator input arrived: the turn ends once its unsafe calls finish."""
    STOPPING = "stopping"


@dataclass(frozen=True, slots=True)
class TurnScope:
    """What a turn attempt may do, fixed when the turn starts."""

    chat_id: UUID
    owner: UUID
    """The chat's owner, whose credentials, rules and sources the turn may use."""
    turn_id: UUID
    attempt: int
    groups: tuple[str, ...]
    mode: Mode
    execute_network: bool
    image_input: bool
    proactive: bool = False
    declined: tuple[str, ...] = ()
    """Capabilities the operator declined; requests for them are refused."""


def chat_scope(chat: Chat, *, turn_id: UUID, attempt: int, image_input: bool) -> TurnScope:
    """Return the scope of a turn of ``chat`` with its current settings."""
    groups = [*chat.tool_groups, ToolGroup.ALWAYS.value]
    if chat.proactive:
        groups.append(ToolGroup.PROACTIVE.value)
    return TurnScope(
        chat_id=chat.id,
        owner=chat.created_by,
        turn_id=turn_id,
        attempt=attempt,
        groups=tuple(groups),
        mode=chat.mode,
        execute_network=chat.execute_network,
        image_input=image_input,
        proactive=chat.proactive,
        declined=tuple(chat.declined_capabilities),
    )


@dataclass(eq=False)
class TurnHandle:
    """A registered turn attempt."""

    scope: TurnScope
    secret: str
    url: str
    trace: Context
    """Trace context of the turn; its tool calls arrive in MCP requests without one."""
    state: TurnState = TurnState.OPEN
    keep_decisions: bool = True
    suspended: set[UUID] = field(default_factory=set)
    superseded: set[UUID] = field(default_factory=set)
    """Actions whose approval wait the interruption cancelled."""
    calls: set[asyncio.Task[mt.CallToolResult]] = field(default_factory=set)
    cancellable: set[asyncio.Task[Any]] = field(default_factory=set)
    """Calls of interrupt-safe tools, cancelled when the turn is interrupted."""
    stopped: asyncio.Event = field(default_factory=asyncio.Event)
    interrupted: asyncio.Event = field(default_factory=asyncio.Event)
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    """Set when the state changes or a call finishes; the runner re-checks then."""

    @property
    def in_flight(self) -> int:
        """Calls that have not returned yet."""
        return sum(1 for call in self.calls if not call.done())


class Gateway:
    """The MCP server and the registry of turns it serves."""

    def __init__(
        self,
        registry: Registry,
        executor: Executor,
        services: Services,
        listener: Listener,
        settings: Settings,
    ) -> None:
        self._registry = registry
        self._executor = executor
        self._actions = executor.actions
        self._redact = executor.redact
        self.services = services
        self._listener = listener
        self._hot_wait_seconds = settings.approval.hot_wait.total_seconds()
        self._stop_grace_seconds = settings.turn.stop_grace.total_seconds()
        self._turns: dict[str, TurnHandle] = {}
        self.base_url = ""
        """``http://127.0.0.1:<port>``, set by the worker once the server listens."""

    def app(self) -> Starlette:
        """Return the ASGI application serving ``/mcp/{turn_secret}``."""
        server: Server[Any] = Server(
            "xavier", version="1", on_list_tools=self._list_tools, on_call_tool=self._call_tool
        )
        return server.streamable_http_app(
            streamable_http_path="/mcp/{turn_secret}",
            stateless_http=True,
            json_response=True,
            host="127.0.0.1",
        )

    def register(self, scope: TurnScope) -> TurnHandle:
        """Register a turn attempt and return its handle with the secret MCP URL."""
        secret = secrets.token_urlsafe(32)
        handle = TurnHandle(
            scope=scope,
            secret=secret,
            url=f"{self.base_url}/mcp/{secret}",
            trace=otel_context.get_current(),
        )
        self._turns[secret] = handle
        return handle

    def revoke(self, handle: TurnHandle) -> None:
        """Stop serving the turn."""
        self._turns.pop(handle.secret, None)

    def interrupt(self, handle: TurnHandle) -> None:
        """End the turn at the next safe point because new operator input arrived.

        New calls are refused with ``turn_interrupted``, approval waits end ``cancelled``
        (``superseded``), job waits return early with the job still running, and calls of
        interrupt-safe tools are cancelled. Other calls run to completion: their side effects
        cannot be taken back.
        """
        if handle.state in {TurnState.STOPPING, TurnState.INTERRUPTING}:
            return
        handle.state = TurnState.INTERRUPTING
        handle.interrupted.set()
        handle.changed.set()
        for call in handle.cancellable:
            call.cancel()

    async def stop(self, handle: TurnHandle, *, keep_decisions: bool) -> None:
        """Stop accepting calls and settle the turn's actions.

        Hot waits end as ``suspended`` when ``keep_decisions``, otherwise as ``cancelled``.
        Running handlers get ``turn.stop_grace`` and are then cancelled.
        """
        handle.state = TurnState.STOPPING
        handle.keep_decisions = keep_decisions
        handle.stopped.set()
        handle.changed.set()
        calls = [call for call in handle.calls if not call.done()]
        if not calls:
            return
        _done, pending = await asyncio.wait(calls, timeout=self._stop_grace_seconds)
        for call in pending:
            call.cancel()
        await asyncio.gather(*pending, return_exceptions=True)

    async def run_approved(self, action_id: UUID, scope: TurnScope) -> mt.CallToolResult | None:
        """Execute an action approved after its turn was suspended, with its stored arguments.

        A ``read`` action left ``running`` by a lost executor runs again. Returns ``None`` when
        the action is no longer approved.
        """
        action = await self._actions.get(action_id)
        expected = {ActionStatus.APPROVED}
        if action.risk is Risk.READ:
            expected.add(ActionStatus.RUNNING)
        if action.status not in expected:
            return None
        tool = self._registry.get(action.tool, scope.groups)
        if tool is None:
            await self._actions.transition(
                action_id,
                {ActionStatus.APPROVED},
                ActionStatus.CANCELLED,
                error_code="denied",
                error="the tool is no longer enabled in this chat",
            )
            return None
        args = tool.args_type.model_validate(action.pending_arguments)
        if not await self._actions.transition(
            action_id, expected, ActionStatus.RUNNING, started_at=func.now()
        ):
            return None
        ctx = ToolContext(
            chat_id=scope.chat_id,
            owner=scope.owner,
            action_id=action_id,
            mode=scope.mode,
            execute_network=scope.execute_network,
            image_input=scope.image_input,
            services=self.services,
            risk=action.risk,
            turn_id=action.turn_id,
            proactive=scope.proactive,
            declined=scope.declined,
        )
        return await self._executor.execute(tool, args, ctx)

    def _handle(self, ctx: ServerRequestContext[Any]) -> TurnHandle:
        request = ctx.request
        secret = request.path_params.get("turn_secret") if isinstance(request, Request) else None
        handle = self._turns.get(secret) if isinstance(secret, str) else None
        if handle is None:
            raise MCPError(mt.INVALID_REQUEST, "unknown turn")
        return handle

    async def _list_tools(
        self, ctx: ServerRequestContext[Any], _params: mt.PaginatedRequestParams | None
    ) -> mt.ListToolsResult:
        handle = self._handle(ctx)
        return mt.ListToolsResult(tools=self._registry.mcp_tools(handle.scope.groups))

    async def _call_tool(
        self, ctx: ServerRequestContext[Any], params: mt.CallToolRequestParams
    ) -> mt.CallToolResult:
        handle = self._handle(ctx)
        call = asyncio.create_task(self.call(handle, params.name, dict(params.arguments or {})))
        handle.calls.add(call)
        call.add_done_callback(lambda _call: handle.changed.set())
        try:
            return await asyncio.shield(call)
        except asyncio.CancelledError:
            if not call.cancelled() or handle.state is not TurnState.INTERRUPTING:
                raise
            return error_result("turn_interrupted", "cancelled: new operator input arrived")

    async def call(
        self, handle: TurnHandle, name: str, arguments: dict[str, JsonValue]
    ) -> mt.CallToolResult:
        """Run the pipeline for one call of the turn, in the trace of the turn."""
        token = otel_context.attach(handle.trace)
        try:
            return await self._call(handle, name, arguments)
        finally:
            otel_context.detach(token)

    async def _call(
        self, handle: TurnHandle, name: str, arguments: dict[str, JsonValue]
    ) -> mt.CallToolResult:
        admitted = await self._admit(handle, name, arguments)
        if isinstance(admitted, mt.CallToolResult):
            return admitted
        tool, args = admitted
        scope = handle.scope
        ctx = ToolContext(
            chat_id=scope.chat_id,
            owner=scope.owner,
            action_id=uuid.uuid7(),
            mode=scope.mode,
            execute_network=scope.execute_network,
            image_input=scope.image_input,
            services=self.services,
            turn_id=scope.turn_id,
            interrupted=handle.interrupted,
            proactive=scope.proactive,
            declined=scope.declined,
        )
        try:
            action, args = await self._record(scope, tool, args, ctx)
        except StaleAttemptError:
            handle.state = TurnState.STOPPING
            return error_result("turn_stopping", "a newer attempt owns this turn")
        ctx = dataclasses.replace(ctx, risk=action.risk)
        return await self._proceed(handle, tool, args, ctx, action)

    async def _admit(
        self, handle: TurnHandle, name: str, arguments: dict[str, JsonValue]
    ) -> tuple[AnyTool, ToolArgs] | mt.CallToolResult:
        """Return the tool and validated arguments, or the refusal for the model."""
        if handle.state is TurnState.STOPPING:
            return error_result("turn_stopping", "the turn is stopping; do not call tools")
        if handle.state is TurnState.INTERRUPTING:
            return error_result(
                "turn_interrupted", "new operator input arrived; end your step without tools"
            )
        if handle.state is TurnState.SUSPENDING:
            return error_result("turn_suspending", "the turn waits for approval; end your step")
        tool = self._registry.get(name, handle.scope.groups)
        if tool is None:
            return error_result("invalid_args", f"unknown tool {name!r}")
        try:
            return tool, tool.args_type.model_validate(arguments)
        except ValidationError as error:
            return error_result("invalid_args", _describe(error))

    async def _proceed(
        self,
        handle: TurnHandle,
        tool: AnyTool,
        args: ToolArgs,
        ctx: ToolContext,
        action: Action,
    ) -> mt.CallToolResult:
        """Answer a denied call, wait for a decision on an ``ask`` call, or execute."""
        if action.status is ActionStatus.DENIED:
            return error_result("denied", action.error or "denied", action.id)
        if action.status is ActionStatus.AWAITING_APPROVAL:
            window = 0.0 if tool.suspends else self._hot_wait_seconds
            decided = await self._hot_wait(handle, action.id, window)
            if decided is not ActionStatus.APPROVED:
                return _undecided(action.id, decided)
            if not await self._actions.transition(
                action.id, {ActionStatus.APPROVED}, ActionStatus.RUNNING, started_at=func.now()
            ):
                return _undecided(action.id, await self._actions.status(action.id))
        current = asyncio.current_task()
        if tool.interrupt_safe and current is not None:
            handle.cancellable.add(current)
            current.add_done_callback(handle.cancellable.discard)
            if handle.state is TurnState.INTERRUPTING:
                current.cancel()
        return await self._executor.execute(tool, args, ctx)

    async def _record(
        self, scope: TurnScope, tool: AnyTool, args: ToolArgs, ctx: ToolContext
    ) -> tuple[Action, ToolArgs]:
        """Normalize and moderate the call, then store its action."""
        rejected: str | None = None
        try:
            args = tool.prepare(args)
            resources = tool.resources(args)
        except ResourceRejectedError as error:
            resources, rejected = [], str(error)
        if rejected is None:
            rejected = await tool.refuse(args, ctx)
        if rejected is None:
            risk = await tool.classify(args, ctx)
            if tool.always_ask:
                effect, rule_id = (Effect.DENY if risk is Risk.FORBIDDEN else Effect.ASK), None
            else:
                rules = await self._actions.rules(scope.chat_id, scope.owner)
                decision = decide(tool.name, resources, risk, scope.mode, rules)
                effect, rule_id = decision.effect, decision.rule_id
        else:
            risk, effect, rule_id = tool.risk, Effect.DENY, None
        status = _STATUS_ON_EFFECT[effect]
        exact: dict[str, JsonValue] = args.model_dump(mode="json")
        shown = self._redact.json(tool.redact(exact, ctx))
        resource_values: JsonValue = [{"kind": r.kind.value, "value": r.value} for r in resources]
        denied = status is ActionStatus.DENIED
        action = Action(
            id=ctx.action_id,
            chat_id=scope.chat_id,
            turn_id=scope.turn_id,
            attempt=scope.attempt,
            tool=tool.name,
            arguments=shown,
            pending_arguments=exact if status is ActionStatus.AWAITING_APPROVAL else None,
            args_hash=args_hash(exact),
            resources=resource_values,
            risk=risk,
            effect=effect,
            rule_id=rule_id,
            status=status,
            error_code="denied" if denied else None,
            error=rejected or ("denied by policy" if denied else None),
            started_at=datetime.now(UTC) if status is ActionStatus.RUNNING else None,
            finished_at=datetime.now(UTC) if denied else None,
        )
        await self._actions.create(
            action,
            {
                "action_id": str(action.id),
                "tool": tool.name,
                "status": status.value,
                "arguments": shown,
                "resources": resource_values,
                "risk": risk.value,
                "effect": effect.value,
                "rule_id": str(rule_id) if rule_id else None,
                "description": tool.description,
                "consequences": tool.consequences,
            },
        )
        return action, args

    async def _hot_wait(self, handle: TurnHandle, action_id: UUID, window: float) -> ActionStatus:
        """Wait for a decision within ``window`` seconds.

        Returns the decided status, or ``suspended`` when the window expired or the turn
        stopped with decisions kept.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + window
        async with self._listener.subscribe() as notifications:
            while True:
                status = await self._actions.status(action_id)
                if status is not ActionStatus.AWAITING_APPROVAL:
                    return status
                remaining = deadline - loop.time()
                if handle.state is TurnState.INTERRUPTING:
                    if await self._actions.supersede([action_id]):
                        handle.superseded.add(action_id)
                        return ActionStatus.CANCELLED
                    continue
                if handle.state is TurnState.STOPPING or remaining <= 0:
                    target = (
                        ActionStatus.SUSPENDED
                        if handle.state is not TurnState.STOPPING or handle.keep_decisions
                        else ActionStatus.CANCELLED
                    )
                    if await self._actions.transition(
                        action_id, {ActionStatus.AWAITING_APPROVAL}, target
                    ):
                        if target is ActionStatus.SUSPENDED:
                            handle.suspended.add(action_id)
                            if handle.state is TurnState.OPEN:
                                handle.state = TurnState.SUSPENDING
                            handle.changed.set()
                        return target
                    continue
                await _wait_any(
                    notifications.get(),
                    handle.stopped.wait(),
                    handle.interrupted.wait(),
                    within=min(_RECHECK_SECONDS, remaining),
                )


def _describe(error: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(map(str, e['loc'])) or 'arguments'}: {e['msg']}" for e in error.errors()
    )


def _undecided(action_id: UUID, status: ActionStatus) -> mt.CallToolResult:
    if status is ActionStatus.SUSPENDED:
        return tool_result({"action_id": str(action_id), "status": "awaiting_approval"})
    return tool_result({"action_id": str(action_id), "status": status.value}, is_error=True)


async def _wait_any(*awaitables: Awaitable[object], within: float) -> None:
    tasks = [asyncio.ensure_future(awaitable) for awaitable in awaitables]
    try:
        await asyncio.wait(tasks, timeout=within, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.gather(*tasks, return_exceptions=True)
