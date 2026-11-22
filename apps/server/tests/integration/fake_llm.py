"""A scripted OpenAI-compatible chat completions server for tests with a real Harness runtime.

Tests queue `Reply` values; each model request takes the next one and streams it. Requests for a
session title are answered with `FakeLLM.title` without consuming the queue.
"""

import asyncio
import contextlib
import json
import socket
import threading
import time
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from typing import Any, Self

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

_TITLE_MARK = "Create a concise title"


@dataclass(frozen=True, slots=True)
class ToolCall:
    """A tool call the model makes; ``name`` is the model-facing name."""

    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Reply:
    """One scripted model response.

    ``text`` is streamed in ``chunks`` pieces with ``delay`` seconds before each piece, so a test
    can interrupt a response mid-stream.
    """

    text: str = ""
    calls: tuple[ToolCall, ...] = ()
    chunks: int = 1
    delay: float = 0.0


class FakeLLM:
    """The scripted server, run in a thread on a free loopback port."""

    def __init__(self, title: str = "Scripted title") -> None:
        self.title = title
        self.requests: list[dict[str, Any]] = []
        """Every non-title request body, in arrival order."""
        self._replies: list[Reply] = []
        self._lock = threading.Lock()
        self._ready = threading.Event()
        self.port = _free_port()
        self._server = uvicorn.Server(
            uvicorn.Config(
                Starlette(routes=[Route("/v1/chat/completions", self._complete, methods=["POST"])]),
                host="127.0.0.1",
                port=self.port,
                log_level="warning",
                lifespan="off",
            )
        )
        self._thread = threading.Thread(target=self._server.run, name="fake-llm", daemon=True)

    @property
    def base_url(self) -> str:
        """The provider ``base_url``."""
        return f"http://127.0.0.1:{self.port}/v1"

    def queue(self, *replies: Reply) -> None:
        """Append replies for the next model requests."""
        with self._lock:
            self._replies.extend(replies)

    def pending(self) -> int:
        """Return how many queued replies are not taken yet."""
        with self._lock:
            return len(self._replies)

    def wait_requests(self, count: int, within: float = 30.0) -> list[dict[str, Any]]:
        """Wait until ``count`` requests arrived and return them."""
        deadline = time.monotonic() + within
        while len(self.requests) < count:
            if time.monotonic() > deadline:
                raise TimeoutError(f"{len(self.requests)} of {count} model requests arrived")
            time.sleep(0.05)
        return self.requests[:count]

    def __enter__(self) -> Self:
        self._thread.start()
        while not self._server.started:
            time.sleep(0.01)
        return self

    def __exit__(self, *_exc: object) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=5)

    async def _complete(self, request: Request) -> JSONResponse | StreamingResponse:
        body = await request.json()
        if _is_title(body):
            reply = Reply(text=self.title)
        else:
            self.requests.append(body)
            with self._lock:
                reply = self._replies.pop(0) if self._replies else Reply(text="Done.")
        if not body.get("stream"):
            return JSONResponse(_completion(reply))
        return StreamingResponse(_stream(reply), media_type="text/event-stream")


def _is_title(body: dict[str, Any]) -> bool:
    return any(
        _TITLE_MARK in str(message.get("content")) for message in body.get("messages", [])[:1]
    )


def _completion(reply: Reply) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": reply.text}
    if reply.calls:
        message["tool_calls"] = _calls(reply)
    return {
        "id": "fake",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": "fake",
        "choices": [{"index": 0, "message": message, "finish_reason": _finish(reply)}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


async def _stream(reply: Reply) -> AsyncIterator[str]:
    def chunk(delta: dict[str, Any], finish: str | None = None) -> str:
        data = {
            "id": "fake",
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": "fake",
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }
        return f"data: {json.dumps(data)}\n\n"

    yield chunk({"role": "assistant", "content": ""})
    for piece in _pieces(reply.text, reply.chunks):
        if reply.delay:
            await asyncio.sleep(reply.delay)
        yield chunk({"content": piece})
    if reply.calls:
        yield chunk({"tool_calls": [{"index": i, **call} for i, call in enumerate(_calls(reply))]})
    yield chunk({}, _finish(reply))
    usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    yield f"data: {json.dumps({'id': 'fake', 'choices': [], 'usage': usage})}\n\n"
    yield "data: [DONE]\n\n"


def _calls(reply: Reply) -> list[dict[str, Any]]:
    return [
        {
            "id": f"call_{index}_{time.monotonic_ns()}",
            "type": "function",
            "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
        }
        for index, call in enumerate(reply.calls)
    ]


def _finish(reply: Reply) -> str:
    return "tool_calls" if reply.calls else "stop"


def _pieces(text: str, chunks: int) -> Iterator[str]:
    if not text:
        return
    size = max(1, -(-len(text) // max(1, chunks)))
    for start in range(0, len(text), size):
        yield text[start : start + size]


def _free_port() -> int:
    with contextlib.closing(socket.socket()) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
