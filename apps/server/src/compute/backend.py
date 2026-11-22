"""The Computer a workspace runs."""

import contextlib
from dataclasses import dataclass
from typing import Protocol

from pydantic import JsonValue

from src.compute.channel import DEFAULT_TIMEOUT, Transport


@dataclass(frozen=True, slots=True)
class ComputerSpec:
    """What a Computer container gets; without volumes it gets empty ``tmpfs`` directories."""

    workspace_volume: str | None
    home_volume: str | None
    labels: dict[str, str]


class Computer(Protocol):
    """A running Computer and its ``computerd`` channel."""

    @property
    def id(self) -> str:
        """Container id."""
        ...

    async def call(
        self,
        command: str,
        args: dict[str, JsonValue] | None = None,
        *,
        token: int | None = None,
        timeout: float = DEFAULT_TIMEOUT,  # noqa: ASYNC109 - mirrors `Channel.call`
    ) -> dict[str, JsonValue]:
        """Run a ``computerd`` command; a lost channel is reopened first."""
        ...

    def vnc(self, *, control: bool) -> contextlib.AbstractAsyncContextManager[Transport]:
        """Open a byte stream to the view-only or the control VNC server."""
        ...

    async def destroy(self) -> None:
        """Close Chromium, then kill and remove the container."""
        ...


class ComputerBackend(Protocol):
    """Creates Computers and reconnects to running ones."""

    async def create(self, spec: ComputerSpec) -> Computer:
        """Start a Computer with its browser running."""
        ...

    async def attach(self, computer_id: str) -> Computer | None:
        """Return a running Computer, or ``None`` if its container is gone."""
        ...
