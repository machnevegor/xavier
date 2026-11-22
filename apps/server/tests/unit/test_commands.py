"""Compound command analysis for policy resources."""

import pytest

from src.policy import commands


@pytest.mark.parametrize(
    ("line", "operations", "read_only"),
    [
        ("ls /workspace", ("ls /workspace",), True),
        ("cat a.txt && wc -l a.txt", ("cat a.txt", "wc -l a.txt"), True),
        ("find /workspace -name '*.py'", ("find /workspace -name '*.py'",), True),
        ("find /workspace -delete", ("find /workspace -delete",), False),
        ("git status", ("git status",), True),
        ("git commit -m x", ("git commit -m x",), False),
        ("cd /workspace/src", ("cd /workspace/src",), True),
        ("cd /tmp", ("cd /tmp",), False),
        ("echo hi | cat", ("echo hi", "cat"), False),
        ("ls $(pwd)", ("ls $(pwd)",), False),
        ("cat <a.txt", ("cat <a.txt",), False),
        ("ls; rm -rf /", ("ls", "rm -rf /"), False),
    ],
)
def test_analyze_splits_and_rates(
    line: str, operations: tuple[str, ...], *, read_only: bool
) -> None:
    result = commands.analyze(line, "/workspace")

    assert result.operations == operations
    assert result.read_only is read_only


def test_quoted_separators_still_split() -> None:
    result = commands.analyze("echo 'a&&b' && ls", "/workspace")

    assert result.operations == ("echo 'a&&b'", "ls")
    assert result.read_only is False
