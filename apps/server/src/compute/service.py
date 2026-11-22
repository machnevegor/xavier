"""The chat's Computer for tools: GUI serialization under the lease and page state."""

import asyncio
import contextlib
from collections import defaultdict
from collections.abc import AsyncGenerator
from datetime import timedelta
from uuid import UUID

from pydantic import JsonValue

from src.compute.channel import ComputerError
from src.leases import USER, Leases, gui_key
from src.policy.urls import Resolver, check_url, system_resolver

GUI_LEASE_TTL = timedelta(minutes=5)
"""Outlives the longest tool handler, so a lost holder frees the GUI on its own."""
GUI_WAIT_SECONDS = 30.0
"""How long a call waits for a GUI call of another holder before it gets ``env_busy``."""
_POLL_SECONDS = 0.2


class ComputeService:
    """GUI lease handling and what the last page snapshot told about the chat's browser."""

    def __init__(self, leases: Leases, resolve: Resolver = system_resolver) -> None:
        self._leases = leases
        self._resolve = resolve
        self._queues: defaultdict[UUID, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._passwords: dict[UUID, frozenset[str]] = {}

    @contextlib.asynccontextmanager
    async def gui(self, chat_id: UUID, holder: str) -> AsyncGenerator[int]:
        """Hold the chat's GUI lease for one call and yield its fencing token.

        Calls on this worker queue in order; a call of another holder is waited for up to
        `GUI_WAIT_SECONDS`.

        Raises
        ------
        ComputerError
            ``env_busy`` while the user controls the Computer or when the wait runs out.
        """
        key = gui_key(chat_id)
        async with self._queues[chat_id]:
            token = await self._acquire(key, holder)
            try:
                yield token
            finally:
                await self._leases.release(key, holder, token)

    async def _acquire(self, key: str, holder: str) -> int:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + GUI_WAIT_SECONDS
        while (token := await self._leases.acquire(key, holder, GUI_LEASE_TTL)) is None:
            current = await self._leases.current(key)
            if current is not None and current.holder == USER:
                raise ComputerError("env_busy", "the user controls the Computer; try later")
            if loop.time() >= deadline:
                raise ComputerError("env_busy", "another call keeps the Computer busy")
            await asyncio.sleep(_POLL_SECONDS)
        return token

    async def check_url(self, url: str) -> None:
        """Check that ``url`` resolves only to public addresses.

        Raises
        ------
        src.policy.urls.ResourceRejectedError
            If it does not.
        """
        await check_url(url, self._resolve)

    def password_fields(self, chat_id: UUID) -> frozenset[str]:
        """Return the element ids of password fields in the chat's last page snapshot."""
        return self._passwords.get(chat_id, frozenset())

    def remember(self, chat_id: UUID, snapshot: dict[str, JsonValue]) -> None:
        """Record the password fields of a new page snapshot."""
        elements = snapshot.get("elements")
        if not isinstance(elements, list):
            raise TypeError("a page snapshot has a list of elements")
        self._passwords[chat_id] = frozenset(
            str(element["id"])
            for element in elements
            if isinstance(element, dict) and element.get("password")
        )
