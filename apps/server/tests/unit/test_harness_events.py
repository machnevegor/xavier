import json
import uuid
from pathlib import Path

from deepseek_harness import JsonObject, Notification

from src.agent.harness_events import (
    AssistantMessage,
    HarnessEvent,
    InboxSpliced,
    SessionIdle,
    SessionTitle,
    StepStart,
    ToolCall,
    ToolCallBlock,
    ToolResult,
    TurnEnd,
    Usage,
    parse,
    parse_event,
)

# A session log recorded by the stage 1 probe: one tool call, then a text answer.
FIXTURE = Path(__file__).parents[1] / "fixtures" / "harness" / "tool_turn.session.jsonl"


def _events() -> list[HarnessEvent]:
    lines = FIXTURE.read_text().splitlines()
    return [event for line in lines if (event := parse_event(json.loads(line))) is not None]


def test_recorded_turn_parses_into_the_runner_events() -> None:
    events = _events()

    assert [type(event) for event in events] == [
        InboxSpliced,
        InboxSpliced,
        StepStart,
        SessionTitle,
        AssistantMessage,
        ToolCall,
        ToolResult,
        StepStart,
        AssistantMessage,
        TurnEnd,
    ]
    assert events[0] == InboxSpliced(("9cd7e45d-d88c-4afa-84bb-7c9edf6db964",))
    assert events[1] == InboxSpliced(())
    assert events[3] == SessionTitle("Call mcp__xavier__slow_echo with text 's", generated=False)
    assert events[-1] == TurnEnd("completed")


def test_tool_call_and_result_are_linked_by_call_id() -> None:
    events = _events()
    call_id = "chatcmpl-tool-805db8155c83351d"
    arguments = '{"text":"slow-ok","seconds":20}'

    request = events[4]
    assert isinstance(request, AssistantMessage)
    assert request.text == ""
    assert request.tool_calls == (ToolCallBlock(call_id, "mcp__xavier__slow_echo", arguments),)
    assert events[5] == ToolCall(call_id, "mcp__xavier__slow_echo", arguments)
    assert events[6] == ToolResult(
        call_id, is_error=False, action_id=None, status=None, text="slow-ok"
    )


def test_assistant_text_and_usage_are_read() -> None:
    answer = _events()[8]

    assert isinstance(answer, AssistantMessage)
    assert answer.text == "slow-ok"
    assert answer.tool_calls == ()
    assert isinstance(answer.usage, Usage)
    assert (answer.usage.input, answer.usage.output) == (112, 5)


def test_gateway_result_exposes_the_action() -> None:
    action_id = uuid.uuid4()
    text = json.dumps({"action_id": str(action_id), "status": "awaiting_approval"})

    event = parse_event(
        {
            "type": "tool/result",
            "data": {
                "message": {
                    "toolCallId": "c1",
                    "content": [{"type": "text", "text": text}],
                    "isError": False,
                }
            },
        }
    )

    assert event == ToolResult(
        "c1", is_error=False, action_id=action_id, status="awaiting_approval", text=text
    )


def test_error_end_reason_and_its_message_are_kept() -> None:
    event: JsonObject = {
        "type": "turn/end",
        "data": {"reason": {"kind": "error", "error": {"message": "x"}}},
    }

    assert parse_event(event) == TurnEnd("error", "x")


def test_notifications_of_other_sessions_and_kinds_are_ignored() -> None:
    event: JsonObject = {"type": "step/start", "data": {"step": 2}}

    assert parse(Notification("session.event", {"sessionId": "s", "event": event}), "s") == (
        StepStart(2)
    )
    assert parse(Notification("session.event", {"sessionId": "t", "event": event}), "s") is None
    assert parse(Notification("session.status", {"sessionId": "s", "status": "idle"}), "s") == (
        SessionIdle()
    )
    assert parse(Notification("session.status", {"sessionId": "s", "status": "busy"}), "s") is None
    assert parse_event({"type": "session/title", "data": {"title": "x"}}) == SessionTitle(
        "x", generated=False
    )


def test_session_title_marks_provider_titles_as_generated() -> None:
    assert parse_event(
        {
            "type": "session/title",
            "data": {"title": "Report", "source": {"kind": "provider"}},
        }
    ) == SessionTitle("Report", generated=True)
