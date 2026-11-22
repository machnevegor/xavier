"""Chat capabilities: what the agent may do, what it may ask the operator to enable.

A capability is a tool group (``observe``, ``execute``, ``compute``), network access of code runs
(``execute_network``) or Proactive mode (``proactive``). The agent asks for one with
``request_capability``; the operator's approval changes only that setting of the chat, and a
decline is remembered so the agent does not ask again.
"""

from pydantic import JsonValue
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Action, Chat, ToolGroup

REQUEST_TOOL = "request_capability"
EXECUTE_NETWORK = "execute_network"
PROACTIVE = "proactive"

_GROUPS = (ToolGroup.OBSERVE.value, ToolGroup.EXECUTE.value, ToolGroup.COMPUTE.value)
ALL = (*_GROUPS, EXECUTE_NETWORK, PROACTIVE)
"""Every capability a chat may have."""


def enabled(chat: Chat) -> list[str]:
    """Return the capabilities the chat has."""
    names = [group for group in _GROUPS if group in chat.tool_groups]
    if chat.execute_network:
        names.append(EXECUTE_NETWORK)
    if chat.proactive:
        names.append(PROACTIVE)
    return names


def describe(chat: Chat) -> dict[str, JsonValue]:
    """Return the data of the ``capabilities`` notice."""
    have = enabled(chat)
    declined = [name for name in chat.declined_capabilities if name not in have]
    available = [name for name in ALL if name not in have and name not in declined]
    return {"enabled": list(have), "available": list(available), "declined": list(declined)}


def grant(chat: Chat, capability: str) -> None:
    """Enable ``capability`` on the chat and forget an earlier decline of it.

    Raises
    ------
    ValueError
        If no such capability exists.
    """
    if capability in _GROUPS:
        chat.tool_groups = sorted({*chat.tool_groups, capability})
    elif capability == EXECUTE_NETWORK:
        chat.execute_network = True
    elif capability == PROACTIVE:
        chat.proactive = True
    else:
        raise ValueError(f"unknown capability {capability!r}")
    chat.declined_capabilities = [name for name in chat.declined_capabilities if name != capability]


def decline(chat: Chat, capability: str) -> None:
    """Remember that the operator declined ``capability``."""
    if capability not in chat.declined_capabilities:
        chat.declined_capabilities = [*chat.declined_capabilities, capability]


async def settle_request(session: AsyncSession, action: Action, *, approved: bool) -> None:
    """Apply the operator's decision on a capability request to its chat.

    Raises
    ------
    ValueError
        If the requested capability no longer exists.
    """
    arguments = action.pending_arguments
    capability = arguments.get("capability") if isinstance(arguments, dict) else None
    if not isinstance(capability, str):
        raise TypeError("a capability request names its capability")
    chat = await session.get_one(Chat, action.chat_id, with_for_update=True)
    if approved:
        grant(chat, capability)
    else:
        decline(chat, capability)


def changed(chat: Chat, before: list[str]) -> bool:
    """Return whether the operator's edit changed the chat's capabilities.

    Declines of the changed capabilities are forgotten: the operator decided on them again.
    """
    toggled = set(before) ^ set(enabled(chat))
    chat.declined_capabilities = [c for c in chat.declined_capabilities if c not in toggled]
    return bool(toggled)
