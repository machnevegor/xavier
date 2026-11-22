"""Policy vocabulary shared by tools, the engine and the API."""

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID


class Risk(StrEnum):
    """Side-effect level of one tool call."""

    READ = "read"
    WRITE = "write"
    SENSITIVE = "sensitive"
    FORBIDDEN = "forbidden"


class Effect(StrEnum):
    """Policy outcome for a tool call."""

    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


class Mode(StrEnum):
    """Chat moderation mode: the effect when no rule matches."""

    AUTONOMOUS = "autonomous"
    SUPERVISED = "supervised"
    READ_ONLY = "read_only"


class ResourceKind(StrEnum):
    """What a policy resource names."""

    HOST = "host"
    URL = "url"
    PATH = "path"
    COMMAND = "command"
    """One operation of a shell command line; a call needs every operation allowed."""


class Scope(StrEnum):
    """What an approval allows beyond the action it decides."""

    ONCE = "once"
    CHAT = "chat"
    """A rule of this chat, shown in its settings."""
    ALWAYS = "always"
    """A global rule, shown in Guardrails."""


class Breadth(StrEnum):
    """Which resources the rule of a scoped approval covers."""

    EXACT = "exact"
    """The action's URLs, hosts, paths or commands as they are."""
    SUBDOMAINS = "subdomains"
    """The action's hosts and their subdomains."""
    PUBLIC_WEB = "public_web"
    """Any public website; private addresses stay refused."""
    PREFIX = "prefix"
    """Everything under the directories of the action's paths."""


@dataclass(frozen=True, slots=True)
class Resource:
    """A host, URL or path a tool call touches."""

    kind: ResourceKind
    value: str


@dataclass(frozen=True, slots=True)
class Rule:
    """A policy rule. ``chat_id`` ``None`` is global."""

    id: UUID
    tool_pattern: str
    resource_pattern: str | None
    chat_id: UUID | None
    effect: Effect
    priority: int


@dataclass(frozen=True, slots=True)
class Decision:
    """The effect for a call and the rule that produced it, if any."""

    effect: Effect
    rule_id: UUID | None
