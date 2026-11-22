"""The sandbox a run executes in."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from pydantic import JsonValue

from src.docker.client import ExecResult


@dataclass(frozen=True, slots=True)
class SandboxSpec:
    """What a run container gets."""

    execution_id: UUID
    chat_id: UUID | None
    workspace_volume: str | None
    """The chat's volume at ``/workspace``; ``None`` mounts a tmpfs (API runs)."""
    network: bool


class Sandbox(Protocol):
    """A started run container."""

    @property
    def id(self) -> str:
        """Container id."""
        ...

    async def put(self, path: str, data: bytes) -> None:
        """Write a file, creating parent directories."""
        ...

    async def run(
        self, command: Sequence[str], *, limit: int, env: Mapping[str, str] | None = None
    ) -> ExecResult:
        """Run ``command`` as the run user and collect output up to ``limit`` per stream.

        ``env`` is set for this command only, not for the container.
        """
        ...

    async def read(self, path: str, *, limit: int) -> bytes | None:
        """Return a regular file of at most ``limit`` bytes, or ``None`` if it is not one."""
        ...

    async def metrics(self) -> dict[str, JsonValue]:
        """Return resource usage of the container."""
        ...

    async def destroy(self) -> None:
        """Kill and remove the container."""
        ...


class SandboxBackend(Protocol):
    """Creates run sandboxes."""

    async def create(self, spec: SandboxSpec) -> Sandbox:
        """Create and start a sandbox."""
        ...
