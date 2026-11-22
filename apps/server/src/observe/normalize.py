"""Normalization of observed content and its fingerprint."""

import hashlib
import json

from jsonpath_ng import JSONPath
from jsonpath_ng.ext import parse
from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator


def compile_path(expression: str) -> JSONPath:
    """Compile a JSONPath expression.

    Raises
    ------
    ValueError
        If the expression is invalid.
    """
    try:
        return parse(expression)
    except Exception as error:
        raise ValueError(f"invalid JSONPath {expression!r}: {error}") from error


class SortRule(BaseModel):
    """Sort the lists at ``path`` by the field ``key`` of their objects."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(description="JSONPath of the lists, e.g. `$.items`.")
    key: str = Field(description="Field of the list objects to sort by.")

    @field_validator("path")
    @classmethod
    def _valid(cls, value: str) -> str:
        compile_path(value)
        return value


class Normalization(BaseModel):
    """Rules that remove noise before the fingerprint."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    ignore: list[str] = Field(
        default=[],
        max_length=50,
        description="JSONPath expressions of fields removed before comparison, "
        "e.g. `$..updated_at`.",
    )
    sort: list[SortRule] = Field(default=[], max_length=20)

    @field_validator("ignore")
    @classmethod
    def _valid(cls, value: list[str]) -> list[str]:
        for expression in value:
            compile_path(expression)
        return value


def normalize(content: JsonValue, rules: Normalization) -> JsonValue:
    """Return ``content`` without ignored fields and with the configured lists sorted.

    ``content`` is changed in place.
    """
    for expression in rules.ignore:
        if isinstance(content, (dict, list)):
            content = compile_path(expression).filter(lambda _: True, content)
    for rule in rules.sort:
        for match in compile_path(rule.path).find(content):
            if isinstance(match.value, list):
                match.value.sort(key=lambda item, key=rule.key: _sort_key(item, key))
    return content


def _sort_key(item: JsonValue, key: str) -> tuple[int, str]:
    """Order objects by ``key`` as canonical JSON; items without it go last."""
    if isinstance(item, dict) and key in item:
        return 0, canonical(item[key]).decode()
    return 1, canonical(item).decode()


def canonical(content: JsonValue) -> bytes:
    """Return the canonical JSON of ``content``: sorted keys, no whitespace, UTF-8."""
    return json.dumps(
        content, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode()


def fingerprint(content: JsonValue) -> str:
    """Return the SHA-256 hex digest of the canonical JSON of ``content``."""
    return hashlib.sha256(canonical(content)).hexdigest()
