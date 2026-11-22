"""Computers as hardened Docker containers of the desktop image."""

import asyncio
import contextlib
import json
import uuid
from collections.abc import AsyncGenerator
from functools import cache
from importlib.resources import files

from pydantic import JsonValue

from src.compute.backend import ComputerSpec
from src.compute.channel import DEFAULT_TIMEOUT, Channel, Transport
from src.config import ComputerSettings
from src.docker.client import SANDBOX_NETWORK, Config, DockerHost
from src.workspace.volumes import HOME_MOUNT, USER, WORKSPACE_MOUNT, mounts

COMPUTERD = ["python3", "/opt/xavier/computerd.py"]
VNC_PORTS = {False: 5900, True: 5901}
"""x11vnc ports on 127.0.0.1 inside the container: view-only and control."""
START_TIMEOUT = 60.0
_SHM_BYTES = 256 * 1024**2
_SCRATCH = "rw,nosuid,nodev,size=256m,uid=1000,gid=1000,mode=0755"


@cache
def seccomp_profile() -> str:
    """Return Docker's default seccomp profile plus the calls of Chromium's own sandbox.

    ``seccomp-default.json`` is ``seccomp/default.json`` of moby/profiles ``seccomp/v0.3.0``.
    Chromium isolates renderers in user and PID namespaces and chroots them; the default
    profile allows ``clone`` with namespace flags, ``unshare`` and ``chroot`` only with
    ``CAP_SYS_ADMIN`` or ``CAP_SYS_CHROOT``, which the container does not get. The kernel
    still confines the new namespaces to the container's unprivileged user.
    """
    profile = json.loads(files(__package__).joinpath("seccomp-default.json").read_text())
    profile["syscalls"].append(
        {"names": ["chroot", "clone", "unshare"], "action": "SCMP_ACT_ALLOW"}
    )
    return json.dumps(profile, separators=(",", ":"))


def computer_config(settings: ComputerSettings, spec: ComputerSpec) -> Config:
    """Return the container of a Computer."""
    tmpfs = {"/tmp": "rw,nosuid,nodev,size=256m,mode=1777"}  # noqa: S108 - a tmpfs mount
    if spec.workspace_volume is None:
        volume_mounts = []
        tmpfs |= {WORKSPACE_MOUNT: _SCRATCH, HOME_MOUNT: _SCRATCH}
    else:
        volume_mounts = mounts(spec.workspace_volume, spec.home_volume)
    return {
        "Image": settings.image,
        "User": USER,
        "Labels": dict(spec.labels),
        "HostConfig": {
            "NetworkMode": SANDBOX_NETWORK,
            "Mounts": volume_mounts,
            "Tmpfs": tmpfs,
            "ShmSize": _SHM_BYTES,
            "ReadonlyRootfs": True,
            "CapDrop": ["ALL"],
            "SecurityOpt": ["no-new-privileges:true", f"seccomp={seccomp_profile()}"],
            "PidsLimit": settings.pids,
            "Memory": int(settings.memory),
            "MemorySwap": int(settings.memory),
            "NanoCpus": int(settings.cpus * 1e9),
        },
    }


class DockerComputer:
    """A Computer container and the channel to its ``computerd``."""

    def __init__(self, docker: DockerHost, container_id: str) -> None:
        self._docker = docker
        self._id = container_id
        self._channel: Channel | None = None
        self._lock = asyncio.Lock()

    @property
    def id(self) -> str:
        """Container id."""
        return self._id

    async def call(
        self,
        command: str,
        args: dict[str, JsonValue] | None = None,
        *,
        token: int | None = None,
        timeout: float = DEFAULT_TIMEOUT,  # noqa: ASYNC109 - mirrors `Channel.call`
    ) -> dict[str, JsonValue]:
        """Run a ``computerd`` command; a lost channel is reopened with the browser started."""
        channel = await self._open()
        return await channel.call(command, args, token=token, timeout=timeout)

    @contextlib.asynccontextmanager
    async def vnc(self, *, control: bool) -> AsyncGenerator[Transport]:
        """Yield a byte stream to a VNC server through ``socat`` in the container."""
        stream = await self._docker.open_exec(
            self._id, ["socat", "STDIO", f"TCP:127.0.0.1:{VNC_PORTS[control]}"]
        )
        try:
            yield stream
        finally:
            await stream.close()

    async def destroy(self) -> None:
        """Close Chromium so the profile is written, then remove the container."""
        if self._channel is not None and not self._channel.closed:
            with contextlib.suppress(Exception):
                await self._channel.call("browser.stop", timeout=30)
            await self._channel.close()
        await self._docker.remove_container(self._id)

    async def _open(self) -> Channel:
        async with self._lock:
            if self._channel is None or self._channel.closed:
                stream = await self._docker.open_exec(self._id, COMPUTERD)
                channel = Channel(stream)
                try:
                    await channel.call("browser.start", timeout=START_TIMEOUT)
                except BaseException:
                    await channel.close()
                    raise
                self._channel = channel
            return self._channel


class DockerComputerBackend:
    """Starts Computers on this worker's Docker Engine."""

    def __init__(self, docker: DockerHost, settings: ComputerSettings) -> None:
        self._docker = docker
        self._settings = settings

    async def create(self, spec: ComputerSpec) -> DockerComputer:
        """Start the container and its browser."""
        container_id = await self._docker.start_container(
            f"xv-computer-{uuid.uuid7()}", computer_config(self._settings, spec)
        )
        computer = DockerComputer(self._docker, container_id)
        try:
            await computer.call("ping")
        except BaseException:
            await self._docker.remove_container(container_id)
            raise
        return computer

    async def attach(self, computer_id: str) -> DockerComputer | None:
        """Return the running container, or ``None``."""
        if not await self._docker.is_running(computer_id):
            return None
        return DockerComputer(self._docker, computer_id)
