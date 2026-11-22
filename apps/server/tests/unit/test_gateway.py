import asyncio
import contextlib
import json
import uuid
from collections.abc import AsyncGenerator, Collection
from datetime import timedelta
from uuid import UUID

import httpx2
import mcp_types as mt
import pytest
from opentelemetry import trace
from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags
from pydantic import JsonValue

from src.agent.gateway import (
    LARGE_RESULT_BYTES,
    Actions,
    Executor,
    Gateway,
    StaleAttemptError,
    TurnHandle,
    TurnScope,
    TurnState,
)
from src.config import Settings
from src.db.models import FINAL_ACTION_STATUSES, Action, ActionStatus, Artifact, ToolGroup
from src.events import Listener
from src.observe.fetch import HttpFetcher
from src.policy.model import Effect, Mode, Risk, Rule
from src.policy.redact import REDACTED, Redactor
from src.storage.artifacts import Artifacts
from src.tools.base import Image, Tool, ToolArgs, ToolContext, ToolResult
from src.tools.observe import WebFetch
from src.tools.registry import Registry
from tests.fakes import services


class FakeListener(Listener):
    def __init__(self) -> None:
        self.queues: set[asyncio.Queue[str | None]] = set()

    @contextlib.asynccontextmanager
    async def subscribe(self) -> AsyncGenerator[asyncio.Queue[str | None]]:
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        self.queues.add(queue)
        try:
            yield queue
        finally:
            self.queues.discard(queue)


class FakeActions(Actions):
    def __init__(self, listener: FakeListener) -> None:
        self.rows: dict[UUID, Action] = {}
        self.policy: list[Rule] = []
        self.attempt = 1
        self._listener = listener

    async def rules(self, chat_id: UUID, owner: UUID) -> list[Rule]:
        del chat_id, owner
        return self.policy

    async def create(self, action: Action, event: dict[str, JsonValue]) -> None:
        if action.attempt != self.attempt:
            raise StaleAttemptError(str(action.turn_id))
        self.rows[action.id] = action

    async def transition(
        self,
        action_id: UUID,
        expected: Collection[ActionStatus],
        new: ActionStatus,
        *,
        event: dict[str, JsonValue] | None = None,
        **values: object,
    ) -> bool:
        action = self.rows[action_id]
        if action.status not in expected:
            return False
        action.status = new
        for name, value in values.items():
            setattr(action, name, value)
        if new in FINAL_ACTION_STATUSES:
            action.pending_arguments = None
        for queue in self._listener.queues:
            queue.put_nowait(str(action_id))
        return True

    async def get(self, action_id: UUID) -> Action:
        return self.rows[action_id]

    def only(self) -> Action:
        [action] = self.rows.values()
        return action


class FakeArtifacts(Artifacts):
    def __init__(self) -> None:
        self.created: list[bytes] = []

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
        self.created.append(data)
        return Artifact(id=uuid.uuid7(), chat_id=chat_id, name=name, size_bytes=len(data))


class EchoArgs(ToolArgs):
    text: str
    size: int = 0
    image: bool = False


class Echo(Tool[EchoArgs]):
    name = "echo"
    groups = frozenset({ToolGroup.OBSERVE})
    description = "Echo the text."
    consequences = "None."
    risk = Risk.READ
    args_type = EchoArgs

    async def run(self, args: EchoArgs, ctx: ToolContext) -> ToolResult:
        images = [Image("image/png", "aW1n")] if args.image else []
        return ToolResult({"text": args.text, "pad": "x" * args.size}, images)


class SendArgs(ToolArgs):
    to: str
    api_key: str


class Send(Tool[SendArgs]):
    name = "send"
    groups = frozenset({ToolGroup.OBSERVE})
    description = "Send a message."
    consequences = "A message is sent."
    risk = Risk.WRITE
    args_type = SendArgs

    def __init__(self) -> None:
        self.sent: list[SendArgs] = []

    async def run(self, args: SendArgs, ctx: ToolContext) -> ToolResult:
        self.sent.append(args)
        return ToolResult({"sent": True})


class PinArgs(ToolArgs):
    pin: str


class Pin(Tool[PinArgs]):
    name = "pin"
    groups = frozenset({ToolGroup.OBSERVE})
    description = "Enter a PIN."
    consequences = "None."
    risk = Risk.READ
    args_type = PinArgs

    def __init__(self) -> None:
        self.risks: list[Risk | None] = []

    def redact(self, arguments: dict[str, JsonValue], ctx: ToolContext) -> dict[str, JsonValue]:
        return {**arguments, "pin": "hidden"}

    async def run(self, args: PinArgs, ctx: ToolContext) -> ToolResult:
        self.risks.append(ctx.risk)
        return ToolResult({"entered": args.pin == "1234"})


class HangArgs(ToolArgs):
    pass


class Hang(Tool[HangArgs]):
    name = "hang"
    groups = frozenset({ToolGroup.OBSERVE})
    description = "Never returns."
    consequences = "Unknown."
    risk = Risk.READ
    args_type = HangArgs

    async def run(self, args: HangArgs, ctx: ToolContext) -> ToolResult:
        await asyncio.Event().wait()
        raise AssertionError


class TraceArgs(ToolArgs):
    pass


class Trace(Tool[TraceArgs]):
    name = "trace"
    groups = frozenset({ToolGroup.OBSERVE})
    description = "Report the trace."
    consequences = "None."
    risk = Risk.READ
    args_type = TraceArgs

    def __init__(self) -> None:
        self.trace_ids: list[int] = []

    async def run(self, args: TraceArgs, ctx: ToolContext) -> ToolResult:
        self.trace_ids.append(trace.get_current_span().get_span_context().trace_id)
        return ToolResult({})


class Stand:
    def __init__(self, *, hot_wait: float = 5.0) -> None:
        settings = Settings.model_validate(
            {
                "worker": {"internal_token": "t"},
                "database": {"password": "p"},
                "s3": {"access_key_id": "a", "secret_access_key": "s"},
                "observe": {"credentials_key": "vBEIpEgikO_ICioUfkcoQ2wQc9mtSBVhuxtZW05nJqM="},
                "agent": {
                    "providers": {"p": {"base_url": "http://llm", "api_key": "k"}},
                    "models": [
                        {
                            "id": "m",
                            "provider": "p",
                            "provider_model": "m",
                            "pricing_per_mtok": {"input": 1, "output": 1},
                        }
                    ],
                    "default_model": "m",
                },
                "approval": {"hot_wait": timedelta(seconds=hot_wait)},
                "turn": {"stop_grace": timedelta(seconds=0.2)},
            }
        )
        listener = FakeListener()
        self.actions = FakeActions(listener)
        self.artifacts = FakeArtifacts()
        self.send = Send()
        self.pin = Pin()
        self.trace = Trace()
        executor = Executor(self.actions, self.artifacts, Redactor(["very-secret-value"]))
        self.gateway = Gateway(
            Registry([Echo(), self.send, self.pin, self.trace, Hang(), WebFetch()]),
            executor,
            services(
                settings=settings,
                artifacts=self.artifacts,
                fetcher=HttpFetcher(transport=httpx2.MockTransport(_unreachable)),
            ),
            listener,
            settings,
        )

    def turn(self, mode: Mode = Mode.SUPERVISED, *, image_input: bool = True) -> TurnHandle:
        scope = TurnScope(
            chat_id=uuid.uuid4(),
            owner=uuid.uuid4(),
            turn_id=uuid.uuid4(),
            attempt=1,
            groups=(ToolGroup.OBSERVE.value,),
            mode=mode,
            execute_network=False,
            image_input=image_input,
        )
        return self.gateway.register(scope)

    def start(
        self, handle: TurnHandle, name: str, arguments: dict[str, JsonValue]
    ) -> asyncio.Task[mt.CallToolResult]:
        """Start a call the way the MCP handler does: tracked by the turn."""
        call = asyncio.create_task(self.gateway.call(handle, name, arguments))
        handle.calls.add(call)
        return call

    async def awaiting(self) -> Action:
        async with asyncio.timeout(5):
            while not self.actions.rows:  # noqa: ASYNC110 - the fake has no change signal
                await asyncio.sleep(0.01)
        return self.actions.only()


def _unreachable(_request: httpx2.Request) -> httpx2.Response:
    raise AssertionError("no request expected")


def _payload(result: mt.CallToolResult) -> dict[str, JsonValue]:
    block = result.content[0]
    assert isinstance(block, mt.TextContent)
    return json.loads(block.text)


def _error_code(result: mt.CallToolResult) -> JsonValue:
    error = _payload(result)["error"]
    assert isinstance(error, dict)
    return error["code"]


async def test_unknown_tools_and_invalid_arguments_are_refused_without_an_action() -> None:
    stand = Stand()
    handle = stand.turn()

    unknown = await stand.gateway.call(handle, "absent", {})
    invalid = await stand.gateway.call(handle, "echo", {"text": "a", "extra": 1})

    assert unknown.is_error
    assert _error_code(unknown) == "invalid_args"
    assert _error_code(invalid) == "invalid_args"
    assert stand.actions.rows == {}


async def test_tool_call_runs_in_the_trace_of_its_turn() -> None:
    stand = Stand()
    turn = SpanContext(0x5EED, 0x7, is_remote=False, trace_flags=TraceFlags(TraceFlags.SAMPLED))
    with trace.use_span(NonRecordingSpan(turn)):
        handle = stand.turn()

    await stand.start(handle, "trace", {})

    assert stand.trace.trace_ids == [0x5EED]


async def test_allowed_call_runs_and_its_result_is_recorded() -> None:
    stand = Stand()

    result = await stand.gateway.call(stand.turn(), "echo", {"text": "hi"})

    action = stand.actions.only()
    assert not result.is_error
    assert _payload(result) == {
        "action_id": str(action.id),
        "status": "succeeded",
        "text": "hi",
        "pad": "",
    }
    assert action.status is ActionStatus.SUCCEEDED
    assert action.effect is Effect.ALLOW


async def test_tool_redaction_hides_arguments_but_the_handler_gets_them_and_the_risk() -> None:
    stand = Stand()

    result = await stand.gateway.call(stand.turn(), "pin", {"pin": "1234"})

    assert _payload(result)["entered"] is True
    assert stand.actions.only().arguments == {"pin": "hidden"}
    assert stand.pin.risks == [Risk.READ]


async def test_rule_denial_does_not_run_the_tool() -> None:
    stand = Stand()
    stand.actions.policy = [Rule(uuid.uuid4(), "send", None, None, Effect.DENY, 0)]

    result = await stand.gateway.call(
        stand.turn(Mode.AUTONOMOUS), "send", {"to": "a", "api_key": "k"}
    )

    assert _error_code(result) == "denied"
    assert stand.actions.only().status is ActionStatus.DENIED
    assert stand.send.sent == []


async def test_rejected_resource_is_denied_before_policy() -> None:
    stand = Stand()

    result = await stand.gateway.call(
        stand.turn(Mode.AUTONOMOUS), "web_fetch", {"url": "file:///etc"}
    )

    action = stand.actions.only()
    assert _error_code(result) == "denied"
    assert action.status is ActionStatus.DENIED
    assert action.error == "only http and https URLs are allowed"


async def test_hot_approval_runs_the_exact_arguments_and_records_them_redacted() -> None:
    stand = Stand()
    call = stand.start(stand.turn(), "send", {"to": "bob", "api_key": "very-secret-value"})
    action = await stand.awaiting()

    assert action.status is ActionStatus.AWAITING_APPROVAL
    assert action.arguments == {"to": "bob", "api_key": REDACTED}
    assert action.pending_arguments == {"to": "bob", "api_key": "very-secret-value"}
    await stand.actions.transition(
        action.id, {ActionStatus.AWAITING_APPROVAL}, ActionStatus.APPROVED
    )
    result = await call

    assert _payload(result)["status"] == "succeeded"
    assert stand.send.sent == [SendArgs(to="bob", api_key="very-secret-value")]
    assert action.status is ActionStatus.SUCCEEDED
    assert action.pending_arguments is None


async def test_hot_rejection_is_returned_as_an_error() -> None:
    stand = Stand()
    call = stand.start(stand.turn(), "send", {"to": "bob", "api_key": "k"})
    action = await stand.awaiting()

    await stand.actions.transition(
        action.id, {ActionStatus.AWAITING_APPROVAL}, ActionStatus.REJECTED
    )
    result = await call

    assert result.is_error
    assert _payload(result) == {"action_id": str(action.id), "status": "rejected"}
    assert stand.send.sent == []


async def test_undecided_call_suspends_the_turn() -> None:
    stand = Stand(hot_wait=0.2)
    handle = stand.turn()

    result = await stand.gateway.call(handle, "send", {"to": "bob", "api_key": "k"})
    later = await stand.gateway.call(handle, "echo", {"text": "x"})

    action = stand.actions.only()
    assert not result.is_error
    assert _payload(result) == {"action_id": str(action.id), "status": "awaiting_approval"}
    assert action.status is ActionStatus.SUSPENDED
    assert handle.suspended == {action.id}
    assert handle.state is TurnState.SUSPENDING
    assert _error_code(later) == "turn_suspending"


@pytest.mark.parametrize(
    ("keep_decisions", "status"),
    [(True, ActionStatus.SUSPENDED), (False, ActionStatus.CANCELLED)],
)
async def test_stop_ends_a_hot_wait(keep_decisions: bool, status: ActionStatus) -> None:  # noqa: FBT001
    stand = Stand()
    handle = stand.turn()
    call = stand.start(handle, "send", {"to": "bob", "api_key": "k"})
    action = await stand.awaiting()

    await stand.gateway.stop(handle, keep_decisions=keep_decisions)

    assert call.done()
    assert action.status is status
    assert _error_code(await stand.gateway.call(handle, "echo", {"text": "x"})) == "turn_stopping"


async def test_stop_cancels_a_running_handler_after_the_grace_period() -> None:
    stand = Stand()
    handle = stand.turn()
    call = stand.start(handle, "hang", {})
    action = await stand.awaiting()

    await stand.gateway.stop(handle, keep_decisions=True)

    assert call.cancelled()
    assert action.status is ActionStatus.UNCERTAIN
    assert action.error_code == "interrupted"


async def test_call_from_a_stale_attempt_stops_the_turn() -> None:
    stand = Stand()
    stand.actions.attempt = 2
    handle = stand.turn()

    result = await stand.gateway.call(handle, "echo", {"text": "x"})

    assert _error_code(result) == "turn_stopping"
    assert handle.state is TurnState.STOPPING
    assert stand.actions.rows == {}


async def test_large_result_is_stored_as_an_artifact() -> None:
    stand = Stand()

    result = await stand.gateway.call(
        stand.turn(), "echo", {"text": "x", "size": LARGE_RESULT_BYTES}
    )

    payload = _payload(result)
    [stored] = stand.artifacts.created
    assert payload["truncated"] is True
    assert payload["size_bytes"] == len(stored)
    assert json.loads(stored)["pad"] == "x" * LARGE_RESULT_BYTES


async def test_images_reach_only_models_with_image_input() -> None:
    stand = Stand()

    with_images = await stand.gateway.call(stand.turn(), "echo", {"text": "x", "image": True})
    without = await stand.gateway.call(
        stand.turn(image_input=False), "echo", {"text": "x", "image": True}
    )

    assert [block.type for block in with_images.content] == ["text", "image"]
    assert [block.type for block in without.content] == ["text"]
    assert "images_omitted" in _payload(without)


async def test_action_approved_after_suspension_runs_with_its_stored_arguments() -> None:
    stand = Stand(hot_wait=0.1)
    handle = stand.turn()
    await stand.gateway.call(handle, "send", {"to": "bob", "api_key": "k"})
    action = stand.actions.only()
    await stand.actions.transition(action.id, {ActionStatus.SUSPENDED}, ActionStatus.APPROVED)

    result = await stand.gateway.run_approved(action.id, handle.scope)
    again = await stand.gateway.run_approved(action.id, handle.scope)

    assert result is not None
    assert _payload(result)["status"] == "succeeded"
    assert again is None
    assert stand.send.sent == [SendArgs(to="bob", api_key="k")]
