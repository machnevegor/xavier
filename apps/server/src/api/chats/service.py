"""Settings of new chats, shared by chats and schedules."""

from src.config import Settings
from src.errors import InvalidRequestError


def stored_max_steps(fields: set[str], value: int | None, default: int) -> int | None:
    """Return the step limit to store on a new chat.

    An omitted field takes ``default``. ``None`` stores no limit.
    """
    if "max_steps" not in fields:
        return default
    return value


_TITLE_CHARS = 80


def resolve_model(settings: Settings, model: str | None, reasoning: str | None) -> tuple[str, str]:
    """Return the model and reasoning level of a new chat, defaults applied."""
    model_id = model or settings.agent.default_model
    try:
        levels = settings.agent.model(model_id).reasoning_levels
    except KeyError:
        raise InvalidRequestError(f"unknown model {model_id!r}") from None
    reasoning = reasoning or next(iter(levels))
    if reasoning not in levels:
        raise InvalidRequestError(
            f"reasoning must be one of {sorted(levels)} for model {model_id!r}"
        )
    return model_id, reasoning


def title(text: str) -> str:
    """Return a chat title from the first non-empty line, without heading, bullet or link marks."""
    for raw in text.splitlines():
        line = raw.strip().lstrip("#").strip()
        if line.startswith(("- ", "* ")):
            line = line[2:].strip()
        if line.startswith("[") and "]" in line:
            line = line[1:].split("]", 1)[0].strip()
        if line:
            return line if len(line) <= _TITLE_CHARS else line[: _TITLE_CHARS - 1] + "…"
    return "Attachment"
