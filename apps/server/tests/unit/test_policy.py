import uuid

import pytest

from src.policy.engine import decide, grant_patterns
from src.policy.model import Breadth, Effect, Mode, Resource, ResourceKind, Risk, Rule
from src.policy.redact import REDACTED, Redactor
from src.policy.urls import (
    Resolver,
    ResourceRejectedError,
    check_url,
    normalize_url,
    workspace_path,
)

HOST = [Resource(ResourceKind.HOST, "example.com")]


def _rule(
    effect: Effect,
    tool: str = "*",
    resource: str | None = None,
    *,
    chat: bool = False,
    priority: int = 0,
) -> Rule:
    return Rule(uuid.uuid4(), tool, resource, uuid.uuid4() if chat else None, effect, priority)


@pytest.mark.parametrize(
    ("mode", "risk", "effect"),
    [
        (Mode.AUTONOMOUS, Risk.WRITE, Effect.ALLOW),
        (Mode.AUTONOMOUS, Risk.SENSITIVE, Effect.ASK),
        (Mode.SUPERVISED, Risk.READ, Effect.ALLOW),
        (Mode.SUPERVISED, Risk.WRITE, Effect.ASK),
        (Mode.READ_ONLY, Risk.READ, Effect.ALLOW),
        (Mode.READ_ONLY, Risk.WRITE, Effect.DENY),
    ],
)
def test_mode_decides_without_matching_rules(mode: Mode, risk: Risk, effect: Effect) -> None:
    decision = decide("web_fetch", HOST, risk, mode, [_rule(Effect.DENY, "execute_*")])

    assert decision.effect is effect
    assert decision.rule_id is None


def test_forbidden_is_denied_even_when_a_rule_allows() -> None:
    decision = decide("t", HOST, Risk.FORBIDDEN, Mode.AUTONOMOUS, [_rule(Effect.ALLOW)])

    assert decision.effect is Effect.DENY


def test_higher_priority_wins() -> None:
    allow = _rule(Effect.ALLOW, priority=10)

    decision = decide("t", HOST, Risk.WRITE, Mode.SUPERVISED, [_rule(Effect.DENY), allow])

    assert decision == decide("t", HOST, Risk.WRITE, Mode.SUPERVISED, [allow])
    assert decision.rule_id == allow.id


def test_more_specific_rule_wins_at_equal_priority() -> None:
    by_resource = _rule(Effect.ALLOW, resource="example.*")
    by_chat = _rule(Effect.DENY, chat=True)

    assert decide("t", HOST, Risk.WRITE, Mode.SUPERVISED, [_rule(Effect.DENY), by_resource]) == (
        decide("t", HOST, Risk.WRITE, Mode.SUPERVISED, [by_resource])
    )
    assert (
        decide("t", HOST, Risk.READ, Mode.SUPERVISED, [by_resource, by_chat]).rule_id == by_chat.id
    )


def test_full_tie_prefers_deny_then_ask() -> None:
    rules = [_rule(Effect.ALLOW), _rule(Effect.ASK), _rule(Effect.DENY)]

    assert decide("t", HOST, Risk.READ, Mode.AUTONOMOUS, rules).effect is Effect.DENY
    assert decide("t", HOST, Risk.READ, Mode.AUTONOMOUS, rules[:2]).effect is Effect.ASK


def test_resource_pattern_must_match_a_resource() -> None:
    rule = _rule(Effect.DENY, resource="*.internal")

    assert decide("t", HOST, Risk.READ, Mode.AUTONOMOUS, [rule]).effect is Effect.ALLOW


def _resolver(*addresses: str) -> Resolver:
    async def resolve(_host: str, _port: int) -> list[str]:
        return list(addresses)

    return resolve


async def test_public_url_is_normalized_and_pinned() -> None:
    checked = await check_url("HTTPS://Example.COM.:443/a?b=1#frag", _resolver("93.184.216.34"))

    assert checked.url == "https://example.com/a?b=1"
    assert (checked.scheme, checked.host, checked.port) == ("https", "example.com", 443)
    assert checked.address == "93.184.216.34"


@pytest.mark.parametrize(
    "addresses",
    [("127.0.0.1",), ("10.0.0.5",), ("93.184.216.34", "169.254.169.254"), ("::ffff:127.0.0.1",)],
)
async def test_name_with_any_internal_address_is_rejected(addresses: tuple[str, ...]) -> None:
    with pytest.raises(ResourceRejectedError, match="non-public"):
        await check_url("http://example.com/", _resolver(*addresses))


@pytest.mark.parametrize("url", ["http://127.0.0.1/", "http://[::1]:8080/", "http://100.64.0.1/"])
async def test_internal_literal_addresses_are_rejected(url: str) -> None:
    with pytest.raises(ResourceRejectedError):
        await check_url(url, _resolver("93.184.216.34"))


@pytest.mark.parametrize(
    "url", ["file:///etc/passwd", "ftp://example.com/", "http://user:pw@example.com/", "http:///"]
)
def test_unsupported_urls_are_rejected(url: str) -> None:
    with pytest.raises(ResourceRejectedError):
        normalize_url(url)


def test_non_default_port_is_kept() -> None:
    assert normalize_url("http://example.com:8080") == (
        "http://example.com:8080/",
        "example.com",
        8080,
    )


@pytest.mark.parametrize(
    ("path", "expected"),
    [("a/b", "/workspace/a/b"), ("/workspace/./a/../b", "/workspace/b"), (".", "/workspace")],
)
def test_workspace_paths_are_confined(path: str, expected: str) -> None:
    assert workspace_path(path) == expected


@pytest.mark.parametrize("path", ["../etc", "/etc/passwd", "/workspace-other", "a\x00b"])
def test_paths_outside_the_workspace_are_rejected(path: str) -> None:
    with pytest.raises(ResourceRejectedError):
        workspace_path(path)


def test_redactor_replaces_configured_secrets_and_known_formats() -> None:
    redact = Redactor(["hunter2", ""])

    text = redact("pw hunter2, key sk-or-v1-abcdefghijklmnop1234, Bearer abc.def")

    assert "hunter2" not in text
    assert "sk-or" not in text
    assert "abc.def" not in text
    assert text.count(REDACTED) == 3


def test_redactor_masks_values_of_secret_like_keys_recursively() -> None:
    redact = Redactor([])

    value = redact.json({"headers": {"Authorization": "x"}, "items": [{"password": "p"}], "n": 1})

    assert value == {
        "headers": {"Authorization": REDACTED},
        "items": [{"password": REDACTED}],
        "n": 1,
    }


def test_grant_patterns_cover_exact_hosts_subdomains_public_web_and_path_prefix() -> None:
    host = [Resource(ResourceKind.HOST, "example.com")]
    url = [Resource(ResourceKind.URL, "https://example.com/a")]
    path = [Resource(ResourceKind.PATH, "/workspace/data/a.csv")]

    assert grant_patterns(host, Breadth.EXACT) == ["example.com"]
    assert grant_patterns(host, Breadth.SUBDOMAINS) == ["example.com", "*.example.com"]
    assert grant_patterns(url, Breadth.PUBLIC_WEB) == ["http*://*"]
    assert grant_patterns(path, Breadth.PREFIX) == ["/workspace/data/*"]
    with pytest.raises(ValueError, match="does not apply"):
        grant_patterns(path, Breadth.PUBLIC_WEB)
