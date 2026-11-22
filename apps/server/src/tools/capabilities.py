"""Requests to enable a capability of the chat."""

from pydantic import Field

from src.agent import capabilities
from src.db.models import Chat, ToolGroup
from src.policy.model import Risk
from src.tools.base import Tool, ToolArgs, ToolContext, ToolResult


class CapabilityArgs(ToolArgs):
    """Arguments of `request_capability`."""

    capability: str = Field(
        max_length=200,
        description=(
            "A capability the `capabilities` notice lists as available: `observe`, `execute`, "
            "`compute`, `execute_network` or `proactive`."
        ),
    )
    reason: str = Field(
        min_length=1, max_length=500, description="Why the task needs it; the user reads this."
    )


class RequestCapability(Tool[CapabilityArgs]):
    """Ask the operator to enable a capability for this chat."""

    name = capabilities.REQUEST_TOOL
    groups = frozenset({ToolGroup.ALWAYS})
    description = (
        "Ask the user to enable a capability for this chat when the task needs it. Your turn "
        "ends while the user decides; if they approve, you continue in a new turn with its "
        "tools. If they decline, do not ask for it again: requests for a declined capability "
        "are refused until the user changes the chat settings."
    )
    consequences = "Enables the capability for this chat only; the agent continues with it."
    risk = Risk.WRITE
    always_ask = True
    suspends = True
    args_type = CapabilityArgs

    async def refuse(self, args: CapabilityArgs, ctx: ToolContext) -> str | None:
        """Refuse declined, unknown and enabled capabilities."""
        if args.capability in ctx.declined:
            return "the user declined this capability for the chat; do not ask again"
        async with ctx.services.sessions() as session:
            chat = await session.get_one(Chat, ctx.chat_id)
        if args.capability not in capabilities.ALL:
            return f"no capability {args.capability!r}; see the `capabilities` notice"
        if args.capability in capabilities.enabled(chat):
            return f"{args.capability!r} is already enabled; its tools arrive with the next turn"
        return None

    async def run(self, args: CapabilityArgs, ctx: ToolContext) -> ToolResult:
        """Report the capability that the approval enabled."""
        del ctx
        return ToolResult({"capability": args.capability, "enabled": True})
