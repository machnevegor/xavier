"""Rule evaluation and the rules of scoped approvals."""

import posixpath
from collections.abc import Iterable, Sequence
from fnmatch import fnmatchcase

from src.policy.model import Breadth, Decision, Effect, Mode, Resource, ResourceKind, Risk, Rule
from src.policy.urls import WORKSPACE_ROOT

PUBLIC_WEB_PATTERN = "http*://*"
"""URLs of any website; tools refuse private addresses whatever the rules say."""

_MODE_DEFAULTS: dict[Mode, dict[Risk, Effect]] = {
    Mode.AUTONOMOUS: {
        Risk.READ: Effect.ALLOW,
        Risk.WRITE: Effect.ALLOW,
        Risk.SENSITIVE: Effect.ASK,
    },
    Mode.SUPERVISED: {Risk.READ: Effect.ALLOW, Risk.WRITE: Effect.ASK, Risk.SENSITIVE: Effect.ASK},
    Mode.READ_ONLY: {Risk.READ: Effect.ALLOW, Risk.WRITE: Effect.DENY, Risk.SENSITIVE: Effect.DENY},
}
_TIE_ORDER = {Effect.DENY: 0, Effect.ASK: 1, Effect.ALLOW: 2}
_WEB = frozenset({ResourceKind.HOST, ResourceKind.URL})
_GLOB_CHARS = str.maketrans({"*": "[*]", "?": "[?]", "[": "[[]"})


def decide(
    tool: str, resources: Sequence[Resource], risk: Risk, mode: Mode, rules: Iterable[Rule]
) -> Decision:
    """Return the effect for a call of ``tool`` on ``resources``.

    ``forbidden`` is always denied. Otherwise the matching rule with the highest priority wins,
    then the more specific one (chat scope, a resource pattern, a longer tool pattern); on a full
    tie ``deny`` beats ``ask`` beats ``allow``. Without a matching rule the chat mode decides.
    Each ``command`` resource is decided on its own and the most restrictive effect applies.
    """
    if risk is Risk.FORBIDDEN:
        return Decision(Effect.DENY, None)
    commands = [r for r in resources if r.kind is ResourceKind.COMMAND]
    if len(commands) > 1:
        rules = list(rules)
        others = [r for r in resources if r.kind is not ResourceKind.COMMAND]
        decisions = [decide(tool, [*others, command], risk, mode, rules) for command in commands]
        return min(decisions, key=lambda decision: _TIE_ORDER[decision.effect])
    matching = [rule for rule in rules if _matches(rule, tool, resources)]
    if not matching:
        return Decision(_MODE_DEFAULTS[mode][risk], None)
    best = min(matching, key=_rank)
    return Decision(best.effect, best.id)


def grant_patterns(resources: Sequence[Resource], breadth: Breadth) -> list[str | None]:
    """Return the resource patterns of the rules a scoped approval creates, one rule each.

    ``None`` covers every resource of the tool.

    Raises
    ------
    ValueError
        If the breadth does not apply to the resources.
    """
    if not resources:
        if breadth is not Breadth.EXACT:
            raise ValueError("the action names no resources to widen")
        return [None]
    kinds = {resource.kind for resource in resources}
    match breadth:
        case Breadth.EXACT:
            # Hosts of URL calls would widen the rule to every URL of the host.
            exact = [r for r in resources if not (r.kind is ResourceKind.HOST and kinds >= _WEB)]
            return [_literal(resource.value) for resource in exact]
        case Breadth.SUBDOMAINS if kinds <= _WEB:
            hosts = sorted({r.value for r in resources if r.kind is ResourceKind.HOST})
            return [
                pattern for host in hosts for pattern in (_literal(host), f"*.{_literal(host)}")
            ]
        case Breadth.PUBLIC_WEB if ResourceKind.URL in kinds and kinds <= _WEB:
            return [PUBLIC_WEB_PATTERN]
        case Breadth.PREFIX if kinds == {ResourceKind.PATH}:
            folders = sorted({_folder(r.value) for r in resources})
            return [f"{_literal(folder)}/*" for folder in folders]
    raise ValueError(f"`{breadth}` does not apply to {', '.join(sorted(kinds))} resources")


def _literal(value: str) -> str:
    return value.translate(_GLOB_CHARS)


def _folder(path: str) -> str:
    return path if path == WORKSPACE_ROOT else posixpath.dirname(path)


def _matches(rule: Rule, tool: str, resources: Sequence[Resource]) -> bool:
    if not fnmatchcase(tool, rule.tool_pattern):
        return False
    if rule.resource_pattern is None:
        return True
    return any(fnmatchcase(resource.value, rule.resource_pattern) for resource in resources)


def _rank(rule: Rule) -> tuple[int, int, int, int, int]:
    return (
        -rule.priority,
        0 if rule.chat_id is not None else 1,
        0 if rule.resource_pattern is not None else 1,
        -len(rule.tool_pattern.replace("*", "")),
        _TIE_ORDER[rule.effect],
    )
