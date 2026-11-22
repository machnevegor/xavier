"""The step limit lives on the chat and is snapshotted for the attempt."""

import uuid
from pathlib import Path
from typing import cast

import pytest
from pydantic import ValidationError

from src.agent.gateway import TurnHandle
from src.agent.runner import Runner, _Progress, _Turn
from src.api.chats.router import _apply_settings
from src.api.chats.schemas import CreateChatArgs, CreateChatInput, UpdateChatInput
from src.api.chats.service import stored_max_steps
from src.config import ModelSettings, Settings
from src.db.models import Chat, ChatStatus, Mode
from src.orchestration.models import ChatTemplate, TurnInput, TurnLimit


def _chat(max_steps: int | None) -> Chat:
    return Chat(
        id=uuid.uuid7(),
        title="t",
        status=ChatStatus.READY,
        model="test",
        reasoning="off",
        mode=Mode.SUPERVISED,
        tool_groups=["observe"],
        execute_network=False,
        budget_usd=5.0,
        spent_usd=0.0,
        max_steps=max_steps,
    )


def _run(max_steps: int | None, steps: int) -> _Turn:
    chat = _chat(max_steps)
    return _Turn(
        turn=TurnInput(chat_id=chat.id, turn_id=uuid.uuid7()),
        attempt=1,
        chat=chat,
        model=cast(ModelSettings, None),
        handle=cast(TurnHandle, None),
        home=Path(),
        budget_usd=5.0,
        max_steps=max_steps,
        progress=_Progress(steps=steps),
    )


def test_omitted_step_limit_uses_the_default() -> None:
    body = CreateChatArgs.model_validate({"data": {"text": "hi"}})

    assert "max_steps" not in body.data.model_fields_set
    assert stored_max_steps(body.data.model_fields_set, body.data.max_steps, 50) == 50


def test_null_step_limit_is_stored_and_zero_is_rejected() -> None:
    body = CreateChatArgs.model_validate({"data": {"text": "hi", "max_steps": None}})

    assert body.data.max_steps is None
    assert stored_max_steps(body.data.model_fields_set, body.data.max_steps, 50) is None
    with pytest.raises(ValidationError):
        CreateChatInput(text="hi", max_steps=0)


def test_patch_keeps_an_omitted_limit_and_null_clears_it() -> None:
    chat = _chat(50)
    settings = cast(Settings, None)

    actor = uuid.uuid4()
    _apply_settings(settings, chat, UpdateChatInput(), actor)
    assert chat.max_steps == 50
    assert chat.updated_by == actor
    _apply_settings(settings, chat, UpdateChatInput(max_steps=None), actor)
    assert chat.max_steps is None
    _apply_settings(settings, chat, UpdateChatInput(max_steps=12), actor)
    assert chat.max_steps == 12


def test_old_schedule_template_defaults_to_50_and_null_stays_open() -> None:
    stored = {
        "title": "Scheduled report",
        "model": "test",
        "reasoning": "off",
        "mode": "supervised",
        "tool_groups": ["observe"],
        "execute_network": False,
        "budget_usd": 1.0,
    }

    assert ChatTemplate.model_validate(stored).max_steps == 50
    assert ChatTemplate.model_validate(stored | {"max_steps": None}).max_steps is None


def test_step_limit_follows_the_chat_snapshot() -> None:
    runner = Runner.__new__(Runner)

    assert runner._limit(_run(2, steps=2), 10) is None  # noqa: SLF001
    assert runner._limit(_run(2, steps=3), 10) is TurnLimit.STEPS  # noqa: SLF001
    assert runner._limit(_run(None, steps=1000), 10) is None  # noqa: SLF001
