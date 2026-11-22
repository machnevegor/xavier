"""Typed view of the Harness notifications the runner relies on."""

import json
from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from deepseek_harness import Notification
from pydantic import JsonValue


@dataclass(frozen=True, slots=True)
class Usage:
    """Token usage of one model response; ``input`` excludes cached input."""

    input: int
    output: int
    cache_read: int


@dataclass(frozen=True, slots=True)
class ToolCallBlock:
    """A tool call requested in an assistant message."""

    id: str
    name: str
    arguments: str


@dataclass(frozen=True, slots=True)
class StepStart:
    """A model step began."""

    step: int


@dataclass(frozen=True, slots=True)
class AssistantMessage:
    """A model response."""

    text: str
    tool_calls: tuple[ToolCallBlock, ...]
    usage: Usage | None
    interrupted: bool = False
    """The runtime closed while the response streamed; ``text`` is its received prefix."""


@dataclass(frozen=True, slots=True)
class SessionTitle:
    """The runtime titled the session; ``generated`` for a model title, not the fallback."""

    title: str
    generated: bool


@dataclass(frozen=True, slots=True)
class ToolCall:
    """Harness started a tool call."""

    call_id: str
    name: str
    arguments: str


@dataclass(frozen=True, slots=True)
class ToolResult:
    """A tool call finished. ``action_id`` comes from the gateway's JSON result."""

    call_id: str
    is_error: bool
    action_id: UUID | None
    status: str | None
    text: str


@dataclass(frozen=True, slots=True)
class TurnEnd:
    """The Harness turn ended; ``reason`` is ``data.reason.kind``."""

    reason: str
    error: str | None = None
    """``data.reason.error.message`` of a turn that ended with an error."""


@dataclass(frozen=True, slots=True)
class InboxSpliced:
    """Input messages entered the session."""

    message_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SessionIdle:
    """The session has no more work."""


type HarnessEvent = (
    StepStart
    | AssistantMessage
    | SessionTitle
    | ToolCall
    | ToolResult
    | TurnEnd
    | InboxSpliced
    | SessionIdle
)


def sequence(notification: Notification) -> int | None:
    """Return the session log sequence number of a session event notification."""
    event = notification.payload.get("event")
    seq = event.get("seq") if isinstance(event, dict) else None
    return seq if isinstance(seq, int) else None


def logged_events(log: bytes, after: int | None) -> list[HarnessEvent]:
    """Return the events of a session log (``session.v4.jsonl``) after sequence ``after``."""
    events: list[HarnessEvent] = []
    for line in log.splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        seq = record.get("seq")
        if after is not None and (not isinstance(seq, int) or seq <= after):
            continue
        event = parse_event(record)
        if event is not None:
            events.append(event)
    return events


def parse(notification: Notification, session_id: str) -> HarnessEvent | None:
    """Return the event of ``notification`` for ``session_id``, or ``None`` if irrelevant."""
    payload = notification.payload
    if payload.get("sessionId") != session_id:
        return None
    if notification.method == "session.status":
        return SessionIdle() if payload.get("status") == "idle" else None
    event = payload.get("event")
    if notification.method != "session.event" or not isinstance(event, dict):
        return None
    return parse_event(event)


def parse_event(event: dict[str, JsonValue]) -> HarnessEvent | None:
    """Return the typed event of a session event object, or ``None`` if irrelevant."""
    data = event.get("data")
    parser = _PARSERS.get(str(event.get("type")))
    return parser(data) if parser is not None and isinstance(data, dict) else None


def _step_start(data: dict[str, JsonValue]) -> StepStart:
    return StepStart(_int(data.get("step")))


def _tool_call(data: dict[str, JsonValue]) -> ToolCall:
    return ToolCall(str(data.get("callId", "")), str(data.get("name", "")), _arguments(data))


def _turn_end(data: dict[str, JsonValue]) -> TurnEnd:
    reason = data.get("reason")
    if not isinstance(reason, dict):
        return TurnEnd("unknown")
    kind, error = reason.get("kind"), reason.get("error")
    message = error.get("message") if isinstance(error, dict) else None
    return TurnEnd(
        str(kind) if kind is not None else "unknown",
        str(message) if message is not None else None,
    )


def _inbox_spliced(data: dict[str, JsonValue]) -> InboxSpliced:
    inserted = data.get("inserted")
    return InboxSpliced(
        tuple(
            str(message["id"])
            for message in (inserted if isinstance(inserted, list) else [])
            if isinstance(message, dict) and "id" in message
        )
    )


def _assistant_message(data: dict[str, JsonValue]) -> AssistantMessage:
    message = data.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    texts: list[str] = []
    calls: list[ToolCallBlock] = []
    for block in content if isinstance(content, list) else []:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            texts.append(str(block.get("text") or ""))
        elif block.get("type") == "tool-call":
            calls.append(
                ToolCallBlock(
                    str(block.get("id", "")), str(block.get("name", "")), _arguments(block)
                )
            )
    usage = data.get("usage")
    return AssistantMessage(
        "".join(texts),
        tuple(calls),
        Usage(
            _int(usage.get("inputTokens")),
            _int(usage.get("outputTokens")),
            _int(usage.get("cacheReadTokens")),
        )
        if isinstance(usage, dict)
        else None,
        interrupted=data.get("interrupted") is True,
    )


def _session_title(data: dict[str, JsonValue]) -> SessionTitle:
    source = data.get("source")
    kind = source.get("kind") if isinstance(source, dict) else None
    return SessionTitle(str(data.get("title") or ""), generated=kind == "provider")


def _tool_result(data: dict[str, JsonValue]) -> ToolResult:
    message = data.get("message")
    if not isinstance(message, dict):
        return ToolResult("", is_error=True, action_id=None, status=None, text="")
    content = message.get("content")
    text = "".join(
        str(block.get("text") or "")
        for block in (content if isinstance(content, list) else [])
        if isinstance(block, dict) and block.get("type") == "text"
    )
    action_id: UUID | None = None
    status: str | None = None
    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        result = None
    if isinstance(result, dict):
        try:
            action_id = UUID(str(result.get("action_id")))
        except ValueError:
            action_id = None
        status = str(result["status"]) if "status" in result else None
    return ToolResult(
        str(message.get("toolCallId", "")),
        is_error=bool(message.get("isError")),
        action_id=action_id,
        status=status,
        text=text,
    )


def _arguments(data: dict[str, JsonValue]) -> str:
    arguments = data.get("arguments")
    return arguments if isinstance(arguments, str) else json.dumps(arguments)


def _int(value: JsonValue) -> int:
    return value if isinstance(value, int) else 0


_PARSERS: dict[str, Callable[[dict[str, JsonValue]], HarnessEvent]] = {
    "step/start": _step_start,
    "assistant/message": _assistant_message,
    "session/title": _session_title,
    "tool/call": _tool_call,
    "tool/result": _tool_result,
    "turn/end": _turn_end,
    "agent/inbox/spliced": _inbox_spliced,
}
