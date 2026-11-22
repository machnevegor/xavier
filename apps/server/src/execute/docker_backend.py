"""Run sandboxes as hardened Docker containers."""

from collections.abc import AsyncIterator, Mapping, Sequence

from aiodocker.types import JSONValue as DockerValue
from pydantic import JsonValue

from src.config import ExecuteSettings
from src.docker.client import (
    LABEL_EXECUTION,
    SANDBOX_NETWORK,
    Config,
    DockerHost,
    ExecFailedError,
    ExecResult,
    Role,
)
from src.execute.backend import Sandbox, SandboxSpec
from src.workspace.volumes import USER, WORKSPACE_MOUNT, mounts

_GRACE_SECONDS = 60
"""The container outlives ``execute.max_duration`` by this much before it exits on its own."""


class DockerSandbox:
    """A run container."""

    def __init__(self, docker: DockerHost, container_id: str) -> None:
        self._docker = docker
        self._id = container_id

    @property
    def id(self) -> str:
        """Container id."""
        return self._id

    async def put(self, path: str, data: bytes) -> None:
        """Write a file, creating parent directories."""

        async def chunks() -> AsyncIterator[bytes]:
            yield data

        script = 'mkdir -p -- "$(dirname -- "$1")" && cat > "$1"'
        await self._docker.stream_in(self._id, ["sh", "-c", script, "sh", path], chunks())

    async def run(
        self, command: Sequence[str], *, limit: int, env: Mapping[str, str] | None = None
    ) -> ExecResult:
        """Run ``command`` with ``env`` and collect its output."""
        return await self._docker.run(
            self._id, command, limit=limit, workdir=WORKSPACE_MOUNT, env=env
        )

    async def read(self, path: str, *, limit: int) -> bytes | None:
        """Return a regular file of at most ``limit`` bytes."""
        stat = await self._docker.run(self._id, ["stat", "-L", "-c", "%F\t%s", path], limit=4096)
        kind, _, size = stat.stdout.decode().strip().partition("\t")
        if stat.exit_code != 0 or kind not in {"regular file", "regular empty file"}:
            return None
        if int(size) > limit:
            return None
        try:
            return await self._docker.check(self._id, ["cat", path], limit=limit)
        except ExecFailedError:
            return None

    async def metrics(self) -> dict[str, JsonValue]:
        """Return memory and CPU usage from the cgroup."""
        return await self._docker.stats(self._id)

    async def destroy(self) -> None:
        """Kill and remove the container."""
        await self._docker.remove_container(self._id)


class DockerSandboxBackend:
    """Creates run containers on this worker's Docker Engine."""

    def __init__(self, docker: DockerHost, execute: ExecuteSettings) -> None:
        self._docker = docker
        self._execute = execute

    async def create(self, spec: SandboxSpec) -> Sandbox:
        """Create and start the run container."""
        execute = self._execute
        tmpfs = {"/tmp": f"rw,nosuid,nodev,size={execute.tmp_size},mode=1777"}  # noqa: S108
        host: dict[str, DockerValue] = {
            "NetworkMode": SANDBOX_NETWORK if spec.network else "none",
            "ReadonlyRootfs": True,
            "CapDrop": ["ALL"],
            "SecurityOpt": ["no-new-privileges:true"],
            "PidsLimit": execute.pids,
            "Memory": int(execute.memory),
            "MemorySwap": int(execute.memory),
            "NanoCpus": int(execute.cpus * 1e9),
            "Runtime": execute.runtime,
        }
        if spec.workspace_volume is not None:
            host["Mounts"] = mounts(spec.workspace_volume)
        else:
            tmpfs[WORKSPACE_MOUNT] = f"rw,nosuid,nodev,size={execute.tmp_size},uid=1000,gid=1000"
        host["Tmpfs"] = dict(tmpfs)
        lifetime = int(execute.max_duration.total_seconds()) + _GRACE_SECONDS
        config: Config = {
            "Image": execute.image,
            "Cmd": ["sleep", str(lifetime)],
            "User": USER,
            "WorkingDir": WORKSPACE_MOUNT,
            "Env": [],
            "Labels": self._docker.labels(
                Role.RUN, spec.chat_id, **{LABEL_EXECUTION: str(spec.execution_id)}
            ),
            "HostConfig": host,
        }
        container_id = await self._docker.start_container(f"xv-run-{spec.execution_id}", config)
        return DockerSandbox(self._docker, container_id)
