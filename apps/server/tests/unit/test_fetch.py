import contextlib

import httpx2
import pytest
from pydantic import JsonValue

from src.compute.backend import Computer, ComputerSpec
from src.compute.channel import Transport
from src.docker.client import DockerHost
from src.observe.fetch import BrowserRenderer, FetchError, HttpFetcher, to_text

ADDRESSES = {"example.com": "93.184.216.34", "internal.example": "10.0.0.7"}


async def _resolve(host: str, _port: int) -> list[str]:
    return [ADDRESSES[host]]


def _fetcher(handler: httpx2.MockTransport) -> HttpFetcher:
    return HttpFetcher(_resolve, transport=handler)


async def test_request_is_pinned_to_the_checked_address() -> None:
    seen: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(
            200,
            text="<title>T</title><p>Hello</p>",
            headers={"content-type": "text/html; charset=utf-8"},
        )

    result = await _fetcher(httpx2.MockTransport(handle)).fetch(
        "https://Example.com/page", max_bytes=1024, within=5
    )

    [request] = seen
    assert request.url.host == "93.184.216.34"
    assert request.headers["host"] == "example.com"
    assert result.url == "https://example.com/page"
    assert result.content_type == "text/html"
    text, title = to_text(result)
    assert title == "T"
    assert "Hello" in text


async def test_redirect_to_an_internal_address_is_denied() -> None:
    def handle(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(302, headers={"location": "http://internal.example/admin"})

    with pytest.raises(FetchError) as error:
        await _fetcher(httpx2.MockTransport(handle)).fetch(
            "https://example.com/", max_bytes=1024, within=5
        )

    assert error.value.code == "denied"


async def test_endless_redirects_fail() -> None:
    def handle(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(301, headers={"location": f"{request.url.path}x"})

    with pytest.raises(FetchError, match="redirects"):
        await _fetcher(httpx2.MockTransport(handle)).fetch(
            "https://example.com/", max_bytes=1024, within=5
        )


async def test_body_is_cut_at_the_size_limit() -> None:
    def handle(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, content=b"x" * 5000, headers={"content-type": "text/plain"})

    result = await _fetcher(httpx2.MockTransport(handle)).fetch(
        "https://example.com/", max_bytes=1000, within=5
    )

    assert result.truncated
    assert len(result.body) == 1000


async def test_json_is_indented() -> None:
    def handle(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json={"a": [1]})

    result = await _fetcher(httpx2.MockTransport(handle)).fetch(
        "https://example.com/", max_bytes=1000, within=5
    )

    assert to_text(result)[0] == '{\n "a": [\n  1\n ]\n}'


class FakeComputer:
    id = "computer"

    def __init__(self) -> None:
        self.actions: list[JsonValue] = []

    async def call(
        self, command: str, args: dict[str, JsonValue] | None = None, **_: object
    ) -> dict[str, JsonValue]:
        assert command == "browser"
        assert args is not None
        actions = args["actions"]
        assert isinstance(actions, list)
        self.actions = actions
        page: JsonValue = {
            "ok": True,
            "url": "https://example.com/",
            "html": "<p/>",
            "truncated": False,
        }
        return {"results": [page]}

    def vnc(self, *, control: bool) -> contextlib.AbstractAsyncContextManager[Transport]:
        raise NotImplementedError

    async def destroy(self) -> None:
        pass


class FakeComputers:
    def __init__(self) -> None:
        self.computer = FakeComputer()

    async def create(self, spec: ComputerSpec) -> Computer:
        del spec
        return self.computer

    async def attach(self, computer_id: str) -> Computer | None:
        raise NotImplementedError


class FakeDocker(DockerHost):
    def __init__(self) -> None:
        self.worker_id = "worker"


async def test_rendering_sends_the_credential_only_to_the_page_origin() -> None:
    computers = FakeComputers()
    renderer = BrowserRenderer(computers, FakeDocker(), _resolve)

    await renderer.render(
        "https://Example.com/",
        headers={"Accept-Language": "en"},
        credential={"Authorization": "Bearer t"},
        max_bytes=1024,
        within=5,
        wait_s=0,
    )

    assert computers.computer.actions[:2] == [
        {"action": "headers", "headers": {"Accept-Language": "en"}},
        {
            "action": "credential",
            "url": "https://example.com/",
            "headers": {"Authorization": "Bearer t"},
        },
    ]
