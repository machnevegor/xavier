"""JSON-lines channel to ``computerd``: requests with ids, responses in any order."""

import asyncio
import contextlib
import itertools
import json
import logging
from typing import Protocol

from pydantic import JsonValue

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 60.0


class Transport(Protocol):
    """A byte stream to a process: its stdout and stdin."""

    async def read(self) -> bytes | None:
        """Return the next chunk, or ``None`` at the end."""
        ...

    async def write(self, data: bytes) -> None:
        """Write to the process."""
        ...

    async def close(self) -> None:
        """Close the stream; the process sees the end of input."""
        ...


class ComputerError(Exception):
    """A command failed inside the Computer; ``code`` comes from ``computerd``."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ChannelClosedError(ComputerError):
    """``computerd`` exited; the commands it had not answered have an unknown outcome."""

    def __init__(self) -> None:
        super().__init__("env_lost", "the connection to the Computer was lost")


class Channel:
    """One ``computerd`` process; concurrent calls share it."""

    def __init__(self, transport: Transport) -> None:
        self._transport = transport
        self._ids = itertools.count(1)
        self._pending: dict[int, asyncio.Future[dict[str, JsonValue]]] = {}
        self._write = asyncio.Lock()
        self._reader = asyncio.create_task(self._read())

    @property
    def closed(self) -> bool:
        """Whether ``computerd`` stopped answering."""
        return self._reader.done()

    async def call(
        self,
        command: str,
        args: dict[str, JsonValue] | None = None,
        *,
        token: int | None = None,
        timeout: float = DEFAULT_TIMEOUT,  # noqa: ASYNC109 - every command has its own bound
    ) -> dict[str, JsonValue]:
        """Run ``command`` and return its result.

        Raises
        ------
        ComputerError
            With the code ``computerd`` reported.
        ChannelClosedError
            If ``computerd`` exited before answering.
        TimeoutError
            If no answer came within ``timeout``.
        """
        if self.closed:
            raise ChannelClosedError
        request_id = next(self._ids)
        future: asyncio.Future[dict[str, JsonValue]] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        request = {"id": request_id, "cmd": command, "args": args or {}, "token": token}
        try:
            async with self._write:
                await self._transport.write(json.dumps(request).encode() + b"\n")
            async with asyncio.timeout(timeout):
                return await future
        finally:
            self._pending.pop(request_id, None)

    async def close(self) -> None:
        """Close the stream and wait for the reader to finish."""
        await self._transport.close()
        with contextlib.suppress(asyncio.CancelledError):
            await self._reader

    async def _read(self) -> None:
        buffer = bytearray()
        try:
            while (chunk := await self._transport.read()) is not None:
                buffer.extend(chunk)
                while (end := buffer.find(b"\n")) >= 0:
                    line = bytes(buffer[:end])
                    del buffer[: end + 1]
                    self._dispatch(line)
        except Exception:
            logger.exception("the computerd channel failed")
        finally:
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(ChannelClosedError())

    def _dispatch(self, line: bytes) -> None:
        try:
            message = json.loads(line)
            future = self._pending.get(message["id"])
        except ValueError, KeyError, TypeError:
            logger.warning("computerd wrote an invalid line: %r", line[:200])
            return
        if future is None or future.done():
            return
        if message.get("ok"):
            future.set_result(message["result"])
        else:
            error = message.get("error") or {}
            future.set_exception(
                ComputerError(str(error.get("code", "failed")), str(error.get("message", "")))
            )
