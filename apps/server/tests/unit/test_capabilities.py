import uuid

import pytest

from src.agent import capabilities
from src.db.models import Chat, ChatStatus, Mode


def _chat(**fields: object) -> Chat:
    defaults: dict[str, object] = {
        "id": uuid.uuid7(),
        "title": "t",
        "status": ChatStatus.READY,
        "model": "test",
        "reasoning": "off",
        "mode": Mode.SUPERVISED,
        "tool_groups": ["observe"],
        "execute_network": False,
        "proactive": False,
        "declined_capabilities": [],
        "budget_usd": 1.0,
        "max_steps": 50,
    }
    return Chat(**(defaults | fields))


def test_grant_enables_group_and_forgets_decline() -> None:
    chat = _chat(declined_capabilities=["execute"])
    capabilities.grant(chat, "execute")
    assert "execute" in chat.tool_groups
    assert chat.declined_capabilities == []


def test_grant_proactive() -> None:
    chat = _chat()
    capabilities.grant(chat, "proactive")
    assert chat.proactive is True


def test_grant_rejects_unknown_capability() -> None:
    with pytest.raises(ValueError, match="unknown capability"):
        capabilities.grant(_chat(), "mcp:docs")


def test_decline_is_remembered_until_settings_change() -> None:
    chat = _chat()
    capabilities.decline(chat, "compute")
    assert chat.declined_capabilities == ["compute"]
    before = capabilities.enabled(chat)
    chat.tool_groups = sorted({*chat.tool_groups, "compute"})
    assert capabilities.changed(chat, before) is True
    assert chat.declined_capabilities == []


def test_describe_lists_available_and_declined() -> None:
    chat = _chat(declined_capabilities=["execute"])
    data = capabilities.describe(chat)
    assert data["enabled"] == ["observe"]
    assert data["declined"] == ["execute"]
    assert data["available"] == ["compute", "execute_network", "proactive"]
