import json
import logging
import re
from collections.abc import Iterator
from typing import Literal

import pytest

from src.config import LoggingSettings
from src.logs import configure_logging

SECRET = "db-password-123"


@pytest.fixture(autouse=True)
def _restore_logging() -> Iterator[None]:
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield
    root.handlers[:] = handlers
    root.setLevel(level)


def _connect() -> None:
    raise ValueError(f"cannot connect with {SECRET}")


def _emit(capsys: pytest.CaptureFixture[str], log_format: Literal["json", "console"]) -> str:
    configure_logging(LoggingSettings(level="INFO", format=log_format), [SECRET])
    logger = logging.getLogger("xavier.test")
    try:
        _connect()
    except ValueError:
        logger.exception("failed: %s, key sk-or-v1-0123456789abcdef0123", SECRET)
    return capsys.readouterr().out


def test_json_lines_are_redacted(capsys: pytest.CaptureFixture[str]) -> None:
    entry = json.loads(_emit(capsys, "json"))

    assert entry["level"] == "ERROR"
    assert entry["logger"] == "xavier.test"
    assert entry["message"] == "failed: [REDACTED], key [REDACTED]"
    assert "ValueError: cannot connect with [REDACTED]" in entry["exception"]
    assert re.search(r"test_logs\.py:\d+$", entry["file"])


def test_console_lines_are_redacted_and_reference_source(
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = _emit(capsys, "console")

    assert SECRET not in output
    assert "sk-or-v1" not in output
    assert 'File "' in output
    assert "test_logs.py" in output


def test_bearer_tokens_are_redacted(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(LoggingSettings(format="json"), [])
    logging.getLogger("xavier.test").warning("Authorization: Bearer abc.def-ghi")

    assert json.loads(capsys.readouterr().out)["message"] == "Authorization: Bearer [REDACTED]"


def test_noisy_libraries_are_quiet(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(LoggingSettings(level="DEBUG", format="json"), [])
    logging.getLogger("uvicorn.access").info("GET /healthz")

    assert capsys.readouterr().out == ""
