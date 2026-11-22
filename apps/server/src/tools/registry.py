"""The tool catalog and its MCP descriptions."""

from collections.abc import Iterable, Sequence
from typing import Any

import mcp_types as mt

from src.db.models import ToolGroup
from src.tools.base import Tool, ToolArgs
from src.tools.capabilities import RequestCapability
from src.tools.compute import Browser, ComputerTool, Screen, Terminal
from src.tools.credentials import RequestCredential
from src.tools.execute import CodeRun
from src.tools.files import Files
from src.tools.jobs import Job
from src.tools.observe import WatchDraft, WatchHistory, WebFetch
from src.tools.wakeups import WakeupTool

# Tools of different argument types live in one catalog; each validates its own arguments.
type AnyTool = Tool[Any]


class Registry:
    """All tools, looked up by name and filtered by the chat's groups."""

    def __init__(self, tools: Sequence[AnyTool]) -> None:
        self._tools = {tool.name: tool for tool in tools}
        if len(self._tools) != len(tools):
            raise ValueError("tool names must be unique")

    def all(self) -> list[AnyTool]:
        """Return every tool."""
        return list(self._tools.values())

    def get(self, name: str, groups: Iterable[str]) -> AnyTool | None:
        """Return the tool ``name`` if one of ``groups`` enables it."""
        tool = self._tools.get(name)
        return tool if tool is not None and tool.groups & effective_groups(groups) else None

    def visible(self, groups: Iterable[str]) -> list[AnyTool]:
        """Return the tools ``groups`` enable."""
        enabled = effective_groups(groups)
        return [tool for tool in self._tools.values() if tool.groups & enabled]

    def mcp_tools(self, groups: Iterable[str]) -> list[mt.Tool]:
        """Return the MCP descriptions of the tools ``groups`` enable."""
        return [
            mt.Tool(
                name=tool.name,
                description=tool.description,
                input_schema=input_schema(tool.args_type),
            )
            for tool in self.visible(groups)
        ]


def default_registry() -> Registry:
    """Return the registry of every Xavier tool."""
    return Registry(
        [
            WebFetch(),
            WatchDraft(),
            WatchHistory(),
            CodeRun(),
            Files(),
            Job(),
            Browser(),
            Screen(),
            Terminal(),
            ComputerTool(),
            RequestCapability(),
            RequestCredential(),
            WakeupTool(),
        ]
    )


def effective_groups(groups: Iterable[str]) -> set[ToolGroup]:
    """Return the enabled groups; ``files`` comes with ``execute`` or ``compute``."""
    enabled = {ToolGroup(group) for group in groups}
    if enabled & {ToolGroup.EXECUTE, ToolGroup.COMPUTE}:
        enabled.add(ToolGroup.FILES)
    return enabled


def input_schema(args_type: type[ToolArgs]) -> dict[str, Any]:
    """Return the JSON Schema of the arguments."""
    return args_type.model_json_schema()
