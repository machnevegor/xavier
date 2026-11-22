"""Logging configuration shared by every Xavier process."""

import json
import logging
import logging.config
from collections.abc import Iterable
from datetime import UTC, datetime

from src.config import LoggingSettings
from src.policy.redact import Redactor

_QUIET_LOGGERS = (
    "uvicorn.access",
    "httpx2",
    "httpcore2",
    "botocore",
    "aiobotocore",
    "aioboto3",
    "mcp",
)


class JsonFormatter(logging.Formatter):
    """One JSON object per line."""

    def __init__(self, redactor: Redactor) -> None:
        super().__init__()
        self._redact = redactor

    def format(self, record: logging.LogRecord) -> str:
        """Render ``record`` as a redacted JSON line."""
        entry = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": self._redact(record.getMessage()),
            "file": f"{record.pathname}:{record.lineno}",
        }
        if record.exc_info:
            entry["exception"] = self._redact(self.formatException(record.exc_info))
        if record.stack_info:
            entry["stack"] = self._redact(self.formatStack(record.stack_info))
        return json.dumps(entry, ensure_ascii=False)


class ConsoleFormatter(logging.Formatter):
    """Readable development format with a ``File "path", line N`` reference IDEs can open."""

    def __init__(self, redactor: Redactor) -> None:
        super().__init__(
            "%(asctime)s %(levelname)-7s %(name)s: %(message)s\n"
            '  File "%(pathname)s", line %(lineno)d'
        )
        self._redact = redactor

    def format(self, record: logging.LogRecord) -> str:
        """Render ``record`` as redacted text."""
        return self._redact(super().format(record))


def configure_logging(settings: LoggingSettings, secrets: Iterable[str]) -> None:
    """Install the single logging configuration of the process.

    ``secrets`` are literal values (tokens, passwords, keys) removed from every record.
    """
    formatter = JsonFormatter if settings.format == "json" else ConsoleFormatter
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {"default": {"()": formatter, "redactor": Redactor(secrets)}},
            "handlers": {
                "stdout": {
                    "class": "logging.StreamHandler",
                    "formatter": "default",
                    "stream": "ext://sys.stdout",
                }
            },
            "root": {"level": settings.level, "handlers": ["stdout"]},
            "loggers": {name: {"level": "WARNING"} for name in _QUIET_LOGGERS},
        }
    )
