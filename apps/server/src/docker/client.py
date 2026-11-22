"""Labeled containers and volumes, the sandbox network and exec streams over ``aiodocker``."""

import asyncio
import contextlib
import logging
from collections.abc import AsyncGenerator, AsyncIterable, AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

import aiodocker
from aiodocker.exceptions import DockerError
from aiodocker.execs import Exec
from aiodocker.stream import Stream
from aiodocker.types import JSONObject
from aiodocker.utils import clean_filters
from aiodocker.volumes import DockerVolume
from pydantic import JsonValue

logger = logging.getLogger(__name__)

LABEL_CHAT = "xavier.chat"
LABEL_WORKER = "xavier.worker"
LABEL_ROLE = "xavier.role"
LABEL_EXECUTION = "xavier.execution"
LABEL_EXPIRES = "xavier.expires"
"""Unix time until which a short-lived container is not an orphan."""
SANDBOX_NETWORK = "xv-sandbox"
"""Bridge of runs with network; ``firewall-init`` filters traffic entering from it."""
_NOT_FOUND = 404
_CONFLICT = 409
_EXEC_POLL_SECONDS = 0.05

type Config = JSONObject


class Role(StrEnum):
    """What a labeled container or volume is for."""

    HOLDER = "holder"
    WORKSPACE = "workspace"
    HOME = "home"
    RUN = "run"
    PROBE = "probe"
    COMPUTER = "computer"
    RENDER = "render"


class ExecFailedError(Exception):
    """A fixed service command exited with a non-zero code."""

    def __init__(self, command: Sequence[str], exit_code: int, stderr: bytes) -> None:
        super().__init__(
            f"{command[0]} exited with {exit_code}: {stderr[-500:].decode(errors='replace')}"
        )
        self.exit_code = exit_code


@dataclass(frozen=True, slots=True)
class ContainerState:
    """Lifecycle of a container as Docker reports it."""

    running: bool
    status: str
    """``created``, ``running``, ``exited``, ``dead``, ..."""
    oom_killed: bool
    exit_code: int
    restarts: int
    started_at: str
    error: str


@dataclass(frozen=True, slots=True)
class ExecResult:
    """Exit code and output of a command, each stream cut at the limit."""

    exit_code: int
    stdout: bytes
    stderr: bytes
    truncated: bool


class ExecStream:
    """A running exec with stdin attached: stdout is read, stderr is logged."""

    def __init__(self, stream: Stream, command: str) -> None:
        self._stream = stream
        self._command = command

    async def read(self) -> bytes | None:
        """Return the next stdout chunk, or ``None`` when the command ended."""
        while (message := await self._stream.read_out()) is not None:
            if message.stream == 1:
                return message.data
            logger.info("%s: %s", self._command, message.data.decode(errors="replace").rstrip())
        return None

    async def write(self, data: bytes) -> None:
        """Write to stdin."""
        await self._stream.write_in(data)

    async def close(self) -> None:
        """Close stdin and the connection."""
        await self._stream.close()


class DockerHost:
    """The Docker Engine of this worker."""

    def __init__(self, docker: aiodocker.Docker, worker_id: str) -> None:
        self._docker = docker
        self.worker_id = worker_id

    def labels(self, role: Role, chat_id: UUID | None = None, **extra: str) -> dict[str, str]:
        """Return the labels of an object owned by this worker."""
        labels = {LABEL_WORKER: self.worker_id, LABEL_ROLE: role.value, **extra}
        if chat_id is not None:
            labels[LABEL_CHAT] = str(chat_id)
        return labels

    async def ensure_network(self) -> None:
        """Create the sandbox bridge if missing: no IPv6, no traffic between containers."""
        with contextlib.suppress(DockerError):
            await self._docker.networks.get(SANDBOX_NETWORK)
            return
        try:
            await self._docker.networks.create(
                {
                    "Name": SANDBOX_NETWORK,
                    "Driver": "bridge",
                    "EnableIPv6": False,
                    "Options": {
                        "com.docker.network.bridge.name": SANDBOX_NETWORK,
                        "com.docker.network.bridge.enable_icc": "false",
                    },
                }
            )
        except DockerError as error:
            if error.status != _CONFLICT:
                raise

    async def network_gateway(self) -> str:
        """Return the gateway address of the sandbox bridge."""
        network = await self._docker.networks.get(SANDBOX_NETWORK)
        info = await network.show()
        return str(info["IPAM"]["Config"][0]["Gateway"])

    async def wait_output(self, container_id: str, *, within: float) -> bytes:
        """Wait for the container to exit and return its stdout."""
        container = self._docker.containers.container(container_id)
        await container.wait(timeout=within)
        lines = await container.log(stdout=True)
        return "".join(lines).encode()

    async def create_volume(self, name: str, labels: Mapping[str, str]) -> None:
        """Create a named volume."""
        await self._docker.volumes.create({"Name": name, "Labels": dict(labels)})

    async def remove_volume(self, name: str) -> None:
        """Remove a volume; a missing one is ignored."""
        try:
            await self._docker.volumes.get(name)
        except DockerError as error:
            if error.status == _NOT_FOUND:
                return
            raise
        volume = DockerVolume(self._docker, name)
        await volume.delete(force=True)

    async def start_container(self, name: str, config: Config) -> str:
        """Create and start a container; return its id."""
        container = await self._docker.containers.create(config, name=name)
        try:
            await container.start()
        except BaseException:
            await self.remove_container(container.id)
            raise
        return container.id

    async def remove_container(self, container_id: str) -> None:
        """Kill and remove a container; a missing one is ignored."""
        try:
            await self._docker.containers.container(container_id).delete(force=True, v=True)
        except DockerError as error:
            if error.status != _NOT_FOUND:
                raise

    async def is_running(self, container_id: str) -> bool:
        """Return whether the container exists and runs."""
        state = await self.state(container_id)
        return state is not None and state.running

    async def state(self, container_id: str) -> ContainerState | None:
        """Return the lifecycle state of the container, or ``None`` if it is gone."""
        try:
            info = await self._docker.containers.container(container_id).show()
        except DockerError as error:
            if error.status == _NOT_FOUND:
                return None
            raise
        state = info["State"]
        return ContainerState(
            running=bool(state["Running"]),
            status=str(state.get("Status", "")),
            oom_killed=bool(state.get("OOMKilled")),
            exit_code=int(state.get("ExitCode") or 0),
            restarts=int(info.get("RestartCount") or 0),
            started_at=str(state.get("StartedAt", "")),
            error=str(state.get("Error", "")),
        )

    async def stats(self, container_id: str) -> dict[str, JsonValue]:
        """Return memory, CPU and process counters of a running container."""
        [sample] = await self._docker.containers.container(container_id).stats(stream=False)
        memory = sample.get("memory_stats") or {}
        cpu = (sample.get("cpu_stats") or {}).get("cpu_usage") or {}
        return {
            "memory_bytes": memory.get("usage"),
            "memory_limit_bytes": memory.get("limit"),
            "cpu_ns": cpu.get("total_usage"),
            "pids": (sample.get("pids_stats") or {}).get("current"),
        }

    async def owned(
        self,
    ) -> tuple[list[tuple[str, dict[str, str]]], list[tuple[str, dict[str, str]]]]:
        """Return the containers and volumes of this worker as ``(id or name, labels)``."""
        filters = {"label": [f"{LABEL_WORKER}={self.worker_id}"]}
        containers = await self._docker.containers.list(all=True, filters=clean_filters(filters))
        volumes = await self._docker.volumes.list(filters=filters)
        return (
            [(container.id, dict(container["Labels"] or {})) for container in containers],
            [
                (volume["Name"], dict(volume.get("Labels") or {}))
                for volume in volumes.get("Volumes") or []
            ],
        )

    async def run(  # noqa: PLR0913 - one exec and its environment
        self,
        container_id: str,
        command: Sequence[str],
        *,
        limit: int,
        user: str = "",
        workdir: str | None = None,
        env: Mapping[str, str] | None = None,
    ) -> ExecResult:
        """Run ``command`` and collect its output up to ``limit`` bytes per stream.

        ``env`` is set for this process only; Docker keeps it with the exec, which ends with
        the run container.
        """
        execution = await self._exec(container_id, command, user=user, workdir=workdir, env=env)
        out, err = bytearray(), bytearray()
        truncated = False
        async with _open(execution) as stream:
            while (message := await stream.read_out()) is not None:
                target = out if message.stream == 1 else err
                room = limit - len(target)
                if len(message.data) > room:
                    truncated = True
                target.extend(message.data[: max(room, 0)])
        return ExecResult(await _exit_code(execution), bytes(out), bytes(err), truncated)

    async def check(
        self, container_id: str, command: Sequence[str], *, limit: int = 1 << 20
    ) -> bytes:
        """Run a fixed service command and return its stdout.

        Raises
        ------
        ExecFailedError
            If it exits with a non-zero code.
        """
        result = await self.run(container_id, command, limit=limit)
        if result.exit_code != 0:
            raise ExecFailedError(command, result.exit_code, result.stderr)
        return result.stdout

    async def stream_out(self, container_id: str, command: Sequence[str]) -> AsyncIterator[bytes]:
        """Yield the stdout of a fixed service command as it is produced.

        Raises
        ------
        ExecFailedError
            After the output, if the command exited with a non-zero code.
        """
        execution = await self._exec(container_id, command)
        err = bytearray()
        async with _open(execution) as stream:
            while (message := await stream.read_out()) is not None:
                if message.stream == 1:
                    yield message.data
                else:
                    err.extend(message.data[-4096:])
        exit_code = await _exit_code(execution)
        if exit_code != 0:
            raise ExecFailedError(command, exit_code, bytes(err))

    async def stream_in(
        self, container_id: str, command: Sequence[str], chunks: AsyncIterable[bytes]
    ) -> None:
        """Feed ``chunks`` to the stdin of a fixed service command and wait for it to exit.

        Raises
        ------
        ExecFailedError
            If the command exits with a non-zero code.
        """
        execution = await self._exec(container_id, command, stdin=True)
        # Closing the stream sends end of input; the output side closes with it.
        async with _open(execution) as stream:
            async for chunk in chunks:
                await stream.write_in(chunk)
        exit_code = await _exit_code(execution)
        if exit_code != 0:
            raise ExecFailedError(command, exit_code, b"")

    async def open_exec(self, container_id: str, command: Sequence[str]) -> ExecStream:
        """Start a long-lived command with stdin attached; the caller closes the stream."""
        execution = await self._exec(container_id, command, stdin=True)
        stream = execution.start(detach=False)
        await stream.__aenter__()
        return ExecStream(stream, command[0])

    async def _exec(  # noqa: PLR0913 - one exec and its environment
        self,
        container_id: str,
        command: Sequence[str],
        *,
        stdin: bool = False,
        user: str = "",
        workdir: str | None = None,
        env: Mapping[str, str] | None = None,
    ) -> Exec:
        container = self._docker.containers.container(container_id)
        return await container.exec(
            list(command),
            stdin=stdin,
            user=user,
            workdir=workdir,
            environment=dict(env) if env else None,
        )


@contextlib.asynccontextmanager
async def _open(execution: Exec) -> AsyncGenerator[Stream]:
    """Attach to the exec; ``Stream`` itself is typed too narrowly for ``async with``."""
    stream = execution.start(detach=False)
    await stream.__aenter__()
    try:
        yield stream
    finally:
        await stream.close()


async def _exit_code(execution: Exec) -> int:
    while (info := await execution.inspect())["Running"]:  # noqa: ASYNC110 - Docker has no exit event for exec
        await asyncio.sleep(_EXEC_POLL_SECONDS)
    return int(info["ExitCode"])
