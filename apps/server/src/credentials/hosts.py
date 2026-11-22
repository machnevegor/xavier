"""Host patterns that bound where a credential may be sent."""

import re
from fnmatch import fnmatchcase

_HOST_PATTERN = re.compile(r"^(\*\.)?([a-z0-9]([a-z0-9-]*[a-z0-9])?\.)+[a-z0-9-]{2,}$")


def host_pattern(value: str) -> str:
    """Return the normalized host pattern: ``example.com`` or ``*.example.com``.

    Raises
    ------
    ValueError
        If ``value`` is not a host name, optionally with a leading ``*.``.
    """
    pattern = value.strip().lower().rstrip(".")
    if not _HOST_PATTERN.fullmatch(pattern):
        raise ValueError(f"{value!r} is not a host name or `*.` and a host name")
    return pattern


def host_allowed(host: str, patterns: list[str]) -> bool:
    """Return whether ``host`` matches one of ``patterns``.

    ``example.com`` matches only itself; ``*.example.com`` matches its subdomains.
    """
    host = host.lower().rstrip(".")
    return bool(host) and any(fnmatchcase(host, pattern.lower()) for pattern in patterns)
