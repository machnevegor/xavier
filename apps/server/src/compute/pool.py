"""Warm pool: ready Computers with empty volumes, so a new workspace skips the cold start."""

import asyncio
import contextlib
import logging
import uuid
from dataclasses import dataclass

from src.compute.backend import Computer, ComputerBackend, ComputerSpec
from src.docker.client import DockerHost, Role
from src.telemetry import COMPUTER_START, traced

logger = logging.getLogger(__name__)

LABEL_POOL = "xavier.pool"
"""Id of the pool process that created a container or volume; others treat it as an orphan."""
_RETRY_SECONDS = 10.0


@dataclass(frozen=True, slots=True)
class Ready:
    """A Computer with Chromium running on its own empty volumes."""

    computer: Computer
    workspace_volume: str
    home_volume: str


class ComputerPool:
    """Keeps ``computer.pool_size`` ready Computers on this host and starts the rest."""

    def __init__(self, backend: ComputerBackend, docker: DockerHost, size: int) -> None:
        self._backend = backend
        self._docker = docker
        self._size = size
        self.id = str(uuid.uuid7())
        self._ready: list[Ready] = []
        self._wanted = asyncio.Event()

    async def run(self) -> None:
        """Refill the pool until cancelled, then destroy the Computers nobody took."""
        try:
            while True:
                while len(self._ready) < self._size:
                    try:
                        self._ready.append(await self._make())
                    except Exception:
                        logger.exception("cannot start a pool Computer")
                        await asyncio.sleep(_RETRY_SECONDS)
                self._wanted.clear()
                await self._wanted.wait()
        finally:
            ready, self._ready = self._ready, []
            for item in ready:
                with contextlib.suppress(Exception):
                    await self.discard(item)

    @property
    def ready_count(self) -> int:
        """Number of ready Computers."""
        return len(self._ready)

    def take(self) -> Ready | None:
        """Return a ready Computer, or ``None`` when the pool is empty."""
        if not self._ready:
            return None
        self._wanted.set()
        return self._ready.pop(0)

    @traced("xavier.computer.start", COMPUTER_START)
    async def start(self, spec: ComputerSpec) -> Computer:
        """Start a Computer on existing volumes: the cold start."""
        return await self._backend.create(spec)

    async def attach(self, computer_id: str) -> Computer | None:
        """Return a running Computer, or ``None`` if its container is gone."""
        return await self._backend.attach(computer_id)

    async def discard(self, item: Ready) -> None:
        """Destroy a pool Computer and its volumes."""
        await item.computer.destroy()
        for volume in (item.workspace_volume, item.home_volume):
            await self._docker.remove_volume(volume)

    async def _make(self) -> Ready:
        suffix = uuid.uuid7()
        workspace_volume, home_volume = f"xv-pool-ws-{suffix}", f"xv-pool-home-{suffix}"
        pool = {LABEL_POOL: self.id}
        await self._docker.create_volume(
            workspace_volume, self._docker.labels(Role.WORKSPACE) | pool
        )
        await self._docker.create_volume(home_volume, self._docker.labels(Role.HOME) | pool)
        try:
            computer = await self._backend.create(
                ComputerSpec(
                    workspace_volume, home_volume, self._docker.labels(Role.COMPUTER) | pool
                )
            )
        except BaseException:
            for volume in (workspace_volume, home_volume):
                await self._docker.remove_volume(volume)
            raise
        return Ready(computer, workspace_volume, home_volume)
