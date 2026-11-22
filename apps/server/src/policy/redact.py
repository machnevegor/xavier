"""Secret redaction for logs, actions and events."""

import re
from collections.abc import Iterable

from pydantic import JsonValue

REDACTED = "[REDACTED]"
_SECRET_PATTERNS = (
    r"sk-[A-Za-z0-9_\-]{16,}",
    r"(?<=[Bb]earer )[A-Za-z0-9._~+/\-]+=*",
)
_SECRET_KEYS = re.compile(
    r"pass(word|wd)?|secret|token|api[_-]?key|authorization|cookie|credential", re.IGNORECASE
)


_MIN_ADDED_SECRET = 6
"""Shorter values added at runtime would redact ordinary text."""


class Redactor:
    """Replace known secret values and common key formats with a marker."""

    def __init__(self, secrets: Iterable[str]) -> None:
        self._secrets = {s for s in secrets if s}
        self._compile()

    def add(self, *secrets: str) -> None:
        """Also redact ``secrets``, such as credential values sent on the agent's behalf."""
        new = {s for s in secrets if len(s) >= _MIN_ADDED_SECRET} - self._secrets
        if new:
            self._secrets |= new
            self._compile()

    def _compile(self) -> None:
        literals = sorted((re.escape(s) for s in self._secrets), key=len, reverse=True)
        self._pattern = re.compile("|".join([*literals, *_SECRET_PATTERNS]))

    def __call__(self, text: str) -> str:
        """Return ``text`` with every secret replaced."""
        return self._pattern.sub(REDACTED, text)

    def json(self, value: JsonValue) -> JsonValue:
        """Return ``value`` with secrets replaced, including values of secret-like keys.

        Keys ending in ``_id`` name records, such as ``credential_id``, and keep their values.
        """
        match value:
            case str():
                return self(value)
            case dict():
                return {
                    key: REDACTED
                    if _SECRET_KEYS.search(key) and not key.endswith("_id") and item
                    else self.json(item)
                    for key, item in value.items()
                }
            case list():
                return [self.json(item) for item in value]
            case _:
                return value
