"""The single resource check for URLs and workspace paths."""

import asyncio
import ipaddress
import posixpath
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

WORKSPACE_ROOT = "/workspace"
_DEFAULT_PORTS = {"http": 80, "https": 443}

type Resolver = Callable[[str, int], Awaitable[list[str]]]


class ResourceRejectedError(ValueError):
    """A URL or path is outside what tools may touch."""


@dataclass(frozen=True, slots=True)
class CheckedUrl:
    """A URL that passed the check, with the address the request must connect to."""

    url: str
    """Normalized URL: lowercase scheme and host, no fragment."""
    scheme: str
    host: str
    port: int
    address: str
    """IP address the request is pinned to; public unless the check allowed private ones."""


async def system_resolver(host: str, port: int) -> list[str]:
    """Resolve ``host`` with the system resolver."""
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(str(info[4][0]) for info in infos))


def is_public_address(address: str) -> bool:
    """Return whether ``address`` is a globally routable unicast address."""
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def normalize_url(url: str) -> tuple[str, str, int]:
    """Return the normalized URL, host and port, or raise `ResourceRejectedError`."""
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError as error:
        raise ResourceRejectedError(f"invalid URL: {error}") from error
    scheme = parts.scheme.lower()
    if scheme not in _DEFAULT_PORTS:
        raise ResourceRejectedError("only http and https URLs are allowed")
    if parts.username is not None or parts.password is not None:
        raise ResourceRejectedError("credentials in URLs are not allowed")
    host = (parts.hostname or "").rstrip(".").lower()
    if not host:
        raise ResourceRejectedError("URL has no host")
    port = port or _DEFAULT_PORTS[scheme]
    netloc = f"[{host}]" if ":" in host else host
    if port != _DEFAULT_PORTS[scheme]:
        netloc = f"{netloc}:{port}"
    return urlunsplit((scheme, netloc, parts.path or "/", parts.query, "")), host, port


async def check_url(
    url: str, resolve: Resolver = system_resolver, *, allow_private: bool = False
) -> CheckedUrl:
    """Check ``url`` and pin it to a public address, or any address with ``allow_private``.

    Every resolved address must be public, so a name with one internal record is rejected.

    Raises
    ------
    ResourceRejectedError
        If the scheme, host or any resolved address is not allowed.
    """
    normalized, host, port = normalize_url(url)
    try:
        addresses = [str(ipaddress.ip_address(host))]
    except ValueError:
        try:
            addresses = await resolve(host, port)
        except OSError as error:
            raise ResourceRejectedError(f"cannot resolve {host}: {error}") from error
    if not addresses:
        raise ResourceRejectedError(f"cannot resolve {host}")
    blocked = [] if allow_private else [a for a in addresses if not is_public_address(a)]
    if blocked:
        raise ResourceRejectedError(f"{host} resolves to a non-public address {blocked[0]}")
    return CheckedUrl(normalized, normalized.split(":", 1)[0], host, port, addresses[0])


def workspace_path(path: str) -> str:
    """Return ``path`` normalized and confined to ``/workspace``; relative paths start there.

    Raises
    ------
    ResourceRejectedError
        If the path leaves ``/workspace`` or contains a NUL byte.
    """
    if "\x00" in path:
        raise ResourceRejectedError("path contains a NUL byte")
    joined = posixpath.normpath(posixpath.join(WORKSPACE_ROOT, path))
    if joined != WORKSPACE_ROOT and not joined.startswith(WORKSPACE_ROOT + "/"):
        raise ResourceRejectedError(f"path is outside {WORKSPACE_ROOT}: {path}")
    return joined
