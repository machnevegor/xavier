"""Fetching web resources with the resource check on every hop."""

import asyncio
import json
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin

import httpx2
from pydantic import JsonValue

from src.compute.backend import ComputerBackend, ComputerSpec
from src.compute.channel import ComputerError
from src.compute.docker_backend import START_TIMEOUT
from src.docker.client import LABEL_EXPIRES, DockerHost, Role
from src.policy.urls import (
    CheckedUrl,
    Resolver,
    ResourceRejectedError,
    check_url,
    system_resolver,
)
from src.telemetry import traced

AUTH_STATUSES = frozenset({401, 403})
"""Statuses with which a service rejects a credential."""

_REDIRECTS = frozenset({301, 302, 303, 307, 308})
_MAX_REDIRECTS = 5
_USER_AGENT = "Xavier/0.1 (+fetch)"
_RENDER_MARGIN = 60.0
"""Seconds a render container outlives its time limit before cleanup treats it as an orphan."""


class FetchError(Exception):
    """The resource could not be fetched within the limits."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class FetchResult:
    """A fetched response."""

    url: str
    """Final URL after redirects."""
    status: int
    content_type: str
    headers: Mapping[str, str]
    body: bytes
    truncated: bool
    """The body reached the size limit and was cut."""


@dataclass(frozen=True, slots=True)
class _Request:
    method: str
    headers: dict[str, str]
    credential: dict[str, str]
    content: bytes | None


class HttpFetcher:
    """HTTP client that checks every URL and redirect and pins the checked address."""

    def __init__(
        self,
        resolve: Resolver = system_resolver,
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        self._resolve = resolve
        self._client = httpx2.AsyncClient(
            transport=transport, follow_redirects=False, trust_env=False
        )

    async def close(self) -> None:
        """Close connections."""
        await self._client.aclose()

    @traced("xavier.observe.fetch")
    async def fetch(  # noqa: PLR0913 - the parts of a request
        self,
        url: str,
        *,
        max_bytes: int,
        within: float,
        method: str = "GET",
        headers: Mapping[str, str] | None = None,
        credential: Mapping[str, str] | None = None,
        content: bytes | None = None,
        follow_redirects: bool = True,
    ) -> FetchResult:
        """Fetch ``url`` within ``within`` seconds following up to five checked redirects.

        ``credential`` headers go only to the host of ``url``, never to a redirect elsewhere.
        A redirect that switches to ``GET`` drops ``content``. Without ``follow_redirects`` a
        redirect is returned as the result.

        Raises
        ------
        FetchError
            With code ``denied`` for a rejected URL or redirect, ``timeout``, or ``failed``.
        """
        request = _Request(method, dict(headers or {}), dict(credential or {}), content)
        try:
            async with asyncio.timeout(within):
                return await self._follow(
                    url, request, max_bytes, redirects=_MAX_REDIRECTS if follow_redirects else 0
                )
        except TimeoutError as error:
            raise FetchError("timeout", f"no response within {within:g} s") from error

    async def _follow(
        self, url: str, request: _Request, max_bytes: int, *, redirects: int
    ) -> FetchResult:
        method, content = request.method, request.content
        origin: tuple[str, str, int] | None = None
        for _ in range(redirects + 1):
            try:
                checked = await check_url(url, self._resolve)
            except ResourceRejectedError as error:
                raise FetchError("denied", str(error)) from error
            origin = origin or (checked.scheme, checked.host, checked.port)
            headers = request.headers
            if (checked.scheme, checked.host, checked.port) == origin:
                headers = {**headers, **request.credential}
            result, location = await self._fetch_once(checked, method, headers, content, max_bytes)
            if location is None or redirects == 0:
                return result
            url = urljoin(checked.url, location)
            if result.status == 303 or (result.status in {301, 302} and method == "POST"):  # noqa: PLR2004
                method, content = "GET", None
        raise FetchError("failed", f"more than {_MAX_REDIRECTS} redirects")

    async def _fetch_once(
        self,
        checked: CheckedUrl,
        method: str,
        headers: dict[str, str],
        content: bytes | None,
        max_bytes: int,
    ) -> tuple[FetchResult, str | None]:
        pinned = httpx2.URL(checked.url).copy_with(host=checked.address)
        default_port = {"http": 80, "https": 443}[checked.scheme]
        host_header = (
            checked.host if checked.port == default_port else f"{checked.host}:{checked.port}"
        )
        request = self._client.build_request(
            method,
            pinned,
            headers={"User-Agent": _USER_AGENT, **headers, "Host": host_header},
            content=content,
            extensions={"sni_hostname": checked.host},
        )
        try:
            response = await self._client.send(request, stream=True)
        except httpx2.HTTPError as error:
            raise FetchError("failed", f"{checked.host}: {error}") from error
        try:
            location = response.headers.get("location")
            if response.status_code in _REDIRECTS and location:
                return self._result(checked, response, b"", truncated=False), location
            body = bytearray()
            truncated = False
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > max_bytes:
                    del body[max_bytes:]
                    truncated = True
                    break
            return self._result(checked, response, bytes(body), truncated=truncated), None
        except httpx2.HTTPError as error:
            raise FetchError("failed", f"{checked.host}: {error}") from error
        finally:
            await response.aclose()

    @staticmethod
    def _result(
        checked: CheckedUrl, response: httpx2.Response, body: bytes, *, truncated: bool
    ) -> FetchResult:
        content_type = response.headers.get("content-type", "application/octet-stream")
        return FetchResult(
            url=checked.url,
            status=response.status_code,
            content_type=content_type.split(";", 1)[0].strip().lower(),
            headers=dict(response.headers),
            body=body,
            truncated=truncated,
        )


class BrowserRenderer:
    """Renders pages in throwaway Computers that have no workspace volumes."""

    def __init__(
        self, backend: ComputerBackend, docker: DockerHost, resolve: Resolver = system_resolver
    ) -> None:
        self._backend = backend
        self._docker = docker
        self._resolve = resolve

    async def render(  # noqa: PLR0913 - the request and its limits
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        credential: Mapping[str, str],
        max_bytes: int,
        within: float,
        wait_s: float,
    ) -> FetchResult:
        """Load ``url``, give scripts ``wait_s`` seconds and return the rendered HTML.

        ``headers`` go with every request of the page; ``credential`` headers only with the
        requests to the origin of ``url``. The status of the response is not observed; a failed
        navigation is an error.

        Raises
        ------
        FetchError
            With code ``denied`` for a rejected URL, ``timeout``, or ``failed``.
        """
        try:
            checked = await check_url(url, self._resolve)
        except ResourceRejectedError as error:
            raise FetchError("denied", str(error)) from error
        expires = time.time() + START_TIMEOUT + within + _RENDER_MARGIN
        labels = self._docker.labels(Role.RENDER) | {LABEL_EXPIRES: str(int(expires))}
        actions: list[JsonValue] = [
            {"action": "headers", "headers": dict(headers)},
            {"action": "credential", "url": checked.url, "headers": dict(credential)},
            {"action": "navigate", "url": checked.url},
            {"action": "wait", "timeout_s": wait_s},
            {"action": "html", "max_chars": max_bytes},
        ]
        computer = None
        try:
            computer = await self._backend.create(ComputerSpec(None, None, labels))
            reply = await computer.call(
                "browser", {"actions": actions, "snapshot": False}, token=1, timeout=within
            )
        except TimeoutError as error:
            raise FetchError("timeout", f"the page did not render within {within:g} s") from error
        except ComputerError as error:
            raise FetchError("failed", f"rendering failed: {error.message}") from error
        finally:
            if computer is not None:
                await computer.destroy()
        results = reply.get("results")
        page = results[-1] if isinstance(results, list) and results else None
        if not isinstance(page, dict):
            raise FetchError("failed", "the Computer returned no page")
        if not page.get("ok"):
            error = page.get("error")
            message = error.get("message") if isinstance(error, dict) else None
            raise FetchError("failed", f"rendering failed: {message}")
        html = str(page["html"]).encode()
        return FetchResult(
            url=str(page["url"]),
            status=200,
            content_type="text/html",
            headers={},
            body=html[:max_bytes],
            truncated=bool(page["truncated"]) or len(html) > max_bytes,
        )


def decode_text(result: FetchResult) -> str:
    """Decode the body using the declared charset, falling back to UTF-8 with replacement."""
    match = re.search(r"charset=([\w.-]+)", result.headers.get("content-type", ""), re.IGNORECASE)
    try:
        return result.body.decode(match.group(1) if match else "utf-8", errors="replace")
    except LookupError:
        return result.body.decode("utf-8", errors="replace")


def to_text(result: FetchResult) -> tuple[str, str | None]:
    """Return readable text and the title: Markdown-like text for HTML, indented JSON, or text.

    Raises
    ------
    FetchError
        With code ``failed`` for binary content.
    """
    content_type = result.content_type
    if content_type in {"text/html", "application/xhtml+xml"}:
        return html_to_text(decode_text(result))
    if content_type == "application/json" or content_type.endswith("+json"):
        text = decode_text(result)
        try:
            return json.dumps(json.loads(text), indent=1, ensure_ascii=False), None
        except json.JSONDecodeError:
            return text, None
    if content_type.startswith("text/") or content_type.endswith(("+xml", "/xml")):
        return decode_text(result), None
    raise FetchError("failed", f"binary content ({content_type}, {len(result.body)} bytes)")


_SKIP = frozenset({"script", "style", "noscript", "template", "svg", "head", "iframe", "canvas"})
_BLOCK = frozenset(
    {
        "p", "div", "section", "article", "main", "header", "footer", "nav", "aside", "form",
        "table", "tr", "ul", "ol", "dl", "dt", "dd", "pre", "blockquote", "figure", "br", "hr",
    }
)  # fmt: skip
_HEADINGS = {"h1": "# ", "h2": "## ", "h3": "### ", "h4": "#### ", "h5": "##### ", "h6": "###### "}
_OPENING = {
    **{tag: "\n\n" + prefix for tag, prefix in _HEADINGS.items()},
    "li": "\n- ",
    "td": " | ",
    "th": " | ",
    "a": "[",
}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title: str | None = None
        self._skip = 0
        self._in_title = False
        self._links: list[str | None] = []
        self._pre = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "title":
            self._in_title = True
        if tag in _SKIP:
            self._skip += 1
        if self._skip:
            return
        if tag == "pre":
            self._pre += 1
        if tag == "a":
            self._links.append(dict(attrs).get("href"))
        elif tag == "img" and (alt := dict(attrs).get("alt")):
            self.parts.append(f"[image: {alt}]")
        self.parts.append(_OPENING.get(tag, "\n" if tag in _BLOCK else ""))

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        if tag in _SKIP:
            self._skip = max(0, self._skip - 1)
            return
        if self._skip:
            return
        if tag == "a" and self._links:
            href = self._links.pop()
            self.parts.append(f"]({href})" if href and not href.startswith("#") else "]")
        elif tag in _HEADINGS or tag in _BLOCK:
            self.parts.append("\n")
            if tag == "pre":
                self._pre = max(0, self._pre - 1)

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title = ((self.title or "") + data).strip() or None
        if self._skip:
            return
        self.parts.append(data if self._pre else re.sub(r"\s+", " ", data))


def html_to_text(html: str) -> tuple[str, str | None]:
    """Return Markdown-like text of ``html`` without scripts and styles, and its title."""
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    text = "".join(parser.parts).replace("[]", "")
    lines = [line.rstrip() for line in text.splitlines()]
    text = re.sub(
        r"\n{3,}", "\n\n", "\n".join(line.lstrip() if line.strip() else "" for line in lines)
    )
    return text.strip(), parser.title
