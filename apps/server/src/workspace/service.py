"""Workspace lifecycle on this worker: activation, checkpoints, hibernation, deletion, files."""

import asyncio
import contextlib
import dataclasses
import logging
import mimetypes
import posixpath
import time
from collections import defaultdict
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from types_aiobotocore_s3.client import S3Client

from src.compute.backend import Computer, ComputerSpec
from src.compute.channel import ComputerError
from src.compute.pool import LABEL_POOL, ComputerPool, Ready
from src.config import Settings
from src.db.engine import Sessions
from src.db.models import (
    Execution,
    ExecutionStatus,
    Workspace,
    WorkspaceStatus,
)
from src.docker.client import (
    LABEL_CHAT,
    LABEL_EXECUTION,
    LABEL_EXPIRES,
    DockerHost,
    ExecFailedError,
    Role,
)
from src.events import NewEvent, append_events
from src.leases import USER, Leases, gui_key
from src.storage.artifacts import Artifacts
from src.storage.blobs import S3Adapter, delete_prefix
from src.telemetry import CHECKPOINT_DURATION, CHECKPOINT_SIZE, WORKSPACE_ACTIVATION, traced
from src.workspace import checkpoints
from src.workspace.status import lifecycle, recent_failures
from src.workspace.volumes import (
    HOME_MOUNT,
    MARKER,
    WORKSPACE_MOUNT,
    holder_config,
    holder_name,
    volume_names,
)

logger = logging.getLogger(__name__)

MAX_FILE_BYTES = 64 * 1024 * 1024
"""Largest file `files export` and `files import` move."""

_PING_TIMEOUT = 10.0
_KEPT_CHECKPOINTS = 2
_UNFINISHED = (ExecutionStatus.PENDING, ExecutionStatus.RUNNING)
_BROWSER_RESTART_TIMEOUT = 60.0


class WorkspaceError(Exception):
    """The workspace cannot serve the call; ``code`` is a tool error code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class Active:
    """An active workspace on this worker."""

    chat_id: UUID
    epoch: int
    workspace_volume: str
    home_volume: str
    holder_id: str
    restored_at: datetime | None = None
    """Checkpoint time when this activation restored a checkpoint."""
    computer: Computer | None = None
    """The Computer, when the caller asked for it."""
    computer_failure: str | None = None
    """Why the previous Computer stopped, when this activation replaced it."""


@dataclass(frozen=True, slots=True)
class Entry:
    """A directory entry."""

    name: str
    kind: str
    """``file``, ``dir``, ``link`` or ``other``."""
    size: int
    modified: float


_KINDS = {"f": "file", "d": "dir", "l": "link"}


class WorkspaceService:
    """Workspaces whose host is this worker."""

    def __init__(  # noqa: PLR0913 - the workspace spans these stores and the Computer
        self,
        settings: Settings,
        sessions: Sessions,
        docker: DockerHost,
        s3: S3Client,
        artifacts: Artifacts,
        *,
        pool: ComputerPool,
        leases: Leases,
    ) -> None:
        self._settings = settings
        self._sessions = sessions
        self._docker = docker
        self._s3 = s3
        self._bucket = settings.s3.artifacts_bucket
        self._artifacts = artifacts
        self._pool = pool
        self._leases = leases
        self._blobs = S3Adapter(s3, self._bucket)
        self._host = settings.worker.id
        self._locks: defaultdict[UUID, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._computers: dict[UUID, Computer] = {}

    @traced("xavier.workspace.activate", WORKSPACE_ACTIVATION)
    async def activate(self, chat_id: UUID, *, computer: bool = False) -> Active:
        """Return the active workspace, activating it on this worker if needed.

        With ``computer`` the Computer runs too: a new activation takes a ready one from the
        pool and uses its volumes; an active workspace starts one on its own volumes.

        Raises
        ------
        WorkspaceError
            ``env_busy`` if it is active on another worker, ``host_full`` if this worker has
            no room and no idle workspace to hibernate.
        """
        async with self._locks[chat_id]:
            row = await self._row(chat_id)
            if row is not None and row.status is WorkspaceStatus.ACTIVE:
                if row.host != self._host:
                    raise WorkspaceError("env_busy", "the workspace is active on another worker")
                if row.holder_id and await self._docker.is_running(row.holder_id):
                    active = _active(row, row.holder_id)
                else:
                    active = await self._start_holder(row, restore=False)
                return await self._with_computer(row, active) if computer else active
            await self._make_room(chat_id)
            ready = self._pool.take() if computer else None
            if ready is not None and not await self._docker.is_running(ready.computer.id):
                await self._pool.discard(ready)
                ready = None
            try:
                claimed = await self._claim(chat_id, (row.epoch if row else 0) + 1, ready)
            except BaseException:
                if ready is not None:
                    await self._pool.discard(ready)
                raise
            try:
                if ready is None:
                    workspace_volume, home_volume = _volumes(claimed)
                    await self._docker.create_volume(
                        workspace_volume, self._docker.labels(Role.WORKSPACE, chat_id)
                    )
                    await self._docker.create_volume(
                        home_volume, self._docker.labels(Role.HOME, chat_id)
                    )
                active = await self._start_holder(
                    claimed, restore=True, computer=ready.computer if ready else None
                )
                if ready is not None:
                    self._computers[chat_id] = ready.computer
                    return dataclasses.replace(active, computer=ready.computer)
                return await self._with_computer(claimed, active) if computer else active
            except BaseException:
                await self._release(claimed, WorkspaceStatus.HIBERNATED)
                raise

    async def _claim(self, chat_id: UUID, epoch: int, ready: Ready | None) -> Workspace:
        """Take the workspace for this worker by CAS, with new volumes or those of ``ready``."""
        if ready is None:
            workspace_volume, home_volume = volume_names(chat_id, epoch)
        else:
            workspace_volume, home_volume = ready.workspace_volume, ready.home_volume
        values = {
            "status": WorkspaceStatus.ACTIVE,
            "host": self._host,
            "epoch": epoch,
            "workspace_volume": workspace_volume,
            "home_volume": home_volume,
            "holder_id": None,
            "computer_id": ready.computer.id if ready else None,
        }
        async with self._sessions.begin() as session:
            claimed = await session.scalar(
                insert(Workspace)
                .values(chat_id=chat_id, size_bytes=0, **values)
                .on_conflict_do_update(
                    index_elements=[Workspace.chat_id],
                    set_={**values, "touched_at": func.now()},
                    where=Workspace.status != WorkspaceStatus.ACTIVE,
                )
                .returning(Workspace.epoch)
            )
        if claimed is None:
            raise WorkspaceError("env_busy", "the workspace was activated elsewhere")
        return await session_row(self._sessions, chat_id)

    async def _with_computer(self, row: Workspace, active: Active) -> Active:
        """Return ``active`` with its Computer, reconnecting to or starting one.

        A Computer whose container is gone is replaced by a new one on the same volumes.
        """
        computer = self._computers.get(row.chat_id)
        if computer is None or computer.id != row.computer_id:
            computer = await self._pool.attach(row.computer_id) if row.computer_id else None
        elif not await self._docker.is_running(computer.id):
            computer = None
        failure = None
        if computer is None:
            if row.computer_id:
                failure = await self._computer_failed(row.chat_id, row.computer_id)
            computer = await self._pool.start(
                ComputerSpec(
                    active.workspace_volume,
                    active.home_volume,
                    self._docker.labels(Role.COMPUTER, row.chat_id),
                )
            )
            if not await self._set_computer(row, computer.id):
                await computer.destroy()
                raise WorkspaceError("env_busy", "the workspace was activated elsewhere")
        self._computers[row.chat_id] = computer
        return dataclasses.replace(active, computer=computer, computer_failure=failure)

    async def _computer_failed(self, chat_id: UUID, computer_id: str) -> str:
        """Record why the Computer stopped, remove its container and return the reason."""
        state = await self._docker.state(computer_id)
        if state is None:
            reason = "its container is gone"
        elif state.oom_killed:
            reason = "it ran out of memory"
        else:
            reason = f"it exited with code {state.exit_code}"
        await self._docker.remove_container(computer_id)
        async with self._sessions.begin() as session:
            await append_events(
                session,
                chat_id,
                [NewEvent("workspace", {"status": "computer_failed", "reason": reason})],
            )
        return reason

    async def status(self, chat_id: UUID) -> dict[str, JsonValue]:
        """Return the lifecycle, usage and health of the workspace and its Computer."""
        row = await self._row(chat_id)
        report = lifecycle(row, self._settings)
        active = await self.active_here(chat_id)
        if row is not None and active is not None:
            workspace = report["workspace"]
            if isinstance(workspace, dict):
                workspace["size_bytes"] = await self.size(active)
            report["computer"] = await self._computer_health(row)
        report["failures"] = await recent_failures(self._sessions, chat_id)
        return report

    async def restart_computer(self, chat_id: UUID) -> Active:
        """Replace the Computer; processes end, ``/workspace`` and the home directory stay."""
        async with self._locks[chat_id]:
            row = await self._row(chat_id)
            if row is not None and row.status is WorkspaceStatus.ACTIVE and row.host == self._host:
                await self._stop_computer(row)
        return await self.activate(chat_id, computer=True)

    async def _computer_health(self, row: Workspace) -> dict[str, JsonValue] | None:
        if row.computer_id is None:
            return None
        state = await self._docker.state(row.computer_id)
        if state is None:
            return {"status": "gone"}
        health: dict[str, JsonValue] = {
            "status": state.status,
            "oom_killed": state.oom_killed,
            "restarts": state.restarts,
            "started_at": state.started_at,
            "cpus": self._settings.computer.cpus,
        }
        if not state.running:
            return health | {"exit_code": state.exit_code, "error": state.error or None}
        health |= await self._docker.stats(row.computer_id)
        computer = self._computers.get(row.chat_id)
        if computer is not None:
            with contextlib.suppress(ComputerError, TimeoutError):
                health |= await computer.call("ping", timeout=_PING_TIMEOUT)
        return health

    async def _set_computer(self, row: Workspace, computer_id: str | None) -> bool:
        async with self._sessions.begin() as session:
            updated = await session.scalar(
                update(Workspace)
                .where(
                    Workspace.chat_id == row.chat_id,
                    Workspace.epoch == row.epoch,
                    Workspace.status == WorkspaceStatus.ACTIVE,
                )
                .values(computer_id=computer_id)
                .returning(Workspace.chat_id)
            )
        return updated is not None

    async def running_computer(self, chat_id: UUID) -> Computer | None:
        """Return the Computer of a workspace active here, without starting one."""
        row = await self._row(chat_id)
        if row is None or row.status is not WorkspaceStatus.ACTIVE or row.host != self._host:
            return None
        if row.computer_id is None:
            return None
        computer = self._computers.get(chat_id)
        if computer is not None and computer.id == row.computer_id:
            return computer
        computer = await self._pool.attach(row.computer_id)
        if computer is not None:
            self._computers[chat_id] = computer
        return computer

    async def get(self, chat_id: UUID) -> Workspace | None:
        """Return the workspace row."""
        return await self._row(chat_id)

    async def active_here(self, chat_id: UUID) -> Active | None:
        """Return the workspace if it is active on this worker, without activating it."""
        row = await self._row(chat_id)
        if row is None or row.status is not WorkspaceStatus.ACTIVE or row.host != self._host:
            return None
        if not row.holder_id or not await self._docker.is_running(row.holder_id):
            return None
        return _active(row, row.holder_id)

    async def touch(self, chat_id: UUID) -> None:
        """Record that a tool call used the workspace."""
        async with self._sessions.begin() as session:
            await session.execute(
                update(Workspace).where(Workspace.chat_id == chat_id).values(touched_at=func.now())
            )

    async def size(self, active: Active) -> int:
        """Measure both volumes, store the size and return it."""
        output = await self._docker.check(
            active.holder_id, ["du", "-s", "-B1", WORKSPACE_MOUNT, HOME_MOUNT]
        )
        size = sum(int(line.split()[0]) for line in output.decode().splitlines() if line.strip())
        async with self._sessions.begin() as session:
            await session.execute(
                update(Workspace)
                .where(Workspace.chat_id == active.chat_id, Workspace.epoch == active.epoch)
                .values(size_bytes=size)
            )
        return size

    async def ensure_room(self, active: Active) -> None:
        """Raise ``workspace_full`` when the volumes exceed ``workspace.size_limit``."""
        size = await self.size(active)
        limit = self._settings.workspace.size_limit
        if size > limit:
            async with self._sessions.begin() as session:
                await append_events(
                    session,
                    active.chat_id,
                    [NewEvent("workspace", {"status": "full", "size_bytes": size, "limit": limit})],
                )
            raise WorkspaceError(
                "workspace_full",
                f"the workspace holds {size} bytes, over the limit of {limit}; delete files",
            )

    async def checkpoint(self, chat_id: UUID) -> bool:
        """Take a checkpoint if the workspace changed; return whether one was taken."""
        async with self._locks[chat_id]:
            row = await self._row(chat_id)
            if row is None or row.status is not WorkspaceStatus.ACTIVE or row.host != self._host:
                return False
            if not row.holder_id or not await self._docker.is_running(row.holder_id):
                return False
            return await self._checkpoint(_active(row, row.holder_id), row.checkpoint)

    async def hibernate(self, chat_id: UUID, idle_ttl: timedelta | None = None) -> bool:
        """Checkpoint and release the workspace; return whether it is no longer active here.

        With ``idle_ttl`` only a workspace untouched for that long is released. Running
        executions, terminal jobs and user control keep it active and restart the idle period.
        The Computer stops first, so the checkpoint holds the profile Chromium wrote on exit.
        """
        async with self._locks[chat_id]:
            row = await self._row(chat_id)
            if row is None or row.status is not WorkspaceStatus.ACTIVE or row.host != self._host:
                return True
            if idle_ttl is not None and row.touched_at > datetime.now(UTC) - idle_ttl:
                return False
            if await self._busy(chat_id):
                await self.touch(chat_id)
                return False
            await self._stop_computer(row)
            if row.holder_id and await self._docker.is_running(row.holder_id):
                await self._checkpoint(_active(row, row.holder_id), row.checkpoint)
            await self._release(row, WorkspaceStatus.HIBERNATED)
            return True

    async def delete(self, chat_id: UUID) -> None:
        """Remove the volumes on this worker if any, every checkpoint, and mark it deleted."""
        async with self._locks[chat_id]:
            row = await self._row(chat_id)
            if row is None:
                return
            if row.status is WorkspaceStatus.ACTIVE and row.host == self._host:
                await self._remove_containers(row)
            await delete_prefix(self._blobs, f"workspaces/{chat_id}/")
            async with self._sessions.begin() as session:
                await session.execute(
                    update(Workspace)
                    .where(Workspace.chat_id == chat_id)
                    .values(
                        status=WorkspaceStatus.DELETED,
                        host=None,
                        holder_id=None,
                        computer_id=None,
                        workspace_volume=None,
                        home_volume=None,
                        checkpoint=None,
                        checkpoint_at=None,
                        size_bytes=0,
                    )
                )

    async def list_files(self, active: Active, path: str) -> list[Entry]:
        """Return the entries of a directory.

        Raises
        ------
        WorkspaceError
            ``not_found`` if it is not a directory.
        """
        result = await self._docker.run(
            active.holder_id,
            ["find", path, "-mindepth", "1", "-maxdepth", "1", "-printf", r"%y\t%s\t%T@\t%f\n"],
            limit=4 * 1024 * 1024,
        )
        if result.exit_code != 0:
            raise WorkspaceError("not_found", f"{path} is not a directory")
        entries = []
        for line in result.stdout.decode(errors="replace").splitlines():
            kind, size, modified, name = line.split("\t", 3)
            entries.append(Entry(name, _KINDS.get(kind, "other"), int(size), float(modified)))
        return sorted(entries, key=lambda entry: (entry.kind != "dir", entry.name))

    async def read_file(self, active: Active, path: str, limit: int) -> tuple[bytes, int]:
        """Return up to ``limit`` bytes of a file and its size.

        Raises
        ------
        WorkspaceError
            ``not_found`` if it is not a regular file.
        """
        stat = await self._docker.run(
            active.holder_id, ["stat", "-L", "-c", "%F\t%s", path], limit=4096
        )
        kind, _, size = stat.stdout.decode().strip().partition("\t")
        if stat.exit_code != 0 or kind not in {"regular file", "regular empty file"}:
            raise WorkspaceError("not_found", f"{path} is not a file")
        head = await self._docker.check(
            active.holder_id, ["head", "-c", str(limit), path], limit=limit
        )
        return head, int(size)

    async def stream_file(self, active: Active, path: str) -> AsyncIterator[bytes]:
        """Yield the content of a file of at most `MAX_FILE_BYTES`."""
        _content, size = await self.read_file(active, path, 0)
        if size > MAX_FILE_BYTES:
            raise WorkspaceError("invalid_args", f"{path} is larger than {MAX_FILE_BYTES} bytes")
        async for chunk in self._docker.stream_out(active.holder_id, ["cat", path]):
            yield chunk

    async def export_file(self, active: Active, path: str) -> UUID:
        """Store a file as an artifact of the chat; return the artifact id."""
        data = b"".join([chunk async for chunk in self.stream_file(active, path)])
        artifact = await self._artifacts.create(
            chat_id=active.chat_id,
            name=posixpath.basename(path),
            content_type=_content_type(path),
            data=data,
            source="export",
        )
        return artifact.id

    async def write_file(self, active: Active, path: str, data: bytes) -> None:
        """Write ``data`` to ``path``, creating parent directories."""
        if len(data) > MAX_FILE_BYTES:
            raise WorkspaceError("invalid_args", f"files over {MAX_FILE_BYTES} bytes are refused")

        async def chunks() -> AsyncIterator[bytes]:
            for start in range(0, len(data), 1 << 20):
                yield data[start : start + (1 << 20)]

        script = 'mkdir -p -- "$(dirname -- "$1")" && cat > "$1"'
        try:
            await self._docker.stream_in(
                active.holder_id, ["sh", "-c", script, "sh", path], chunks()
            )
        except ExecFailedError as error:
            raise WorkspaceError("failed", f"cannot write {path}") from error

    async def cleanup_orphans(self) -> None:
        """Remove containers and volumes of this worker that no workspace, run or pool owns."""
        async with self._sessions() as session:
            rows = (
                await session.scalars(
                    select(Workspace).where(
                        Workspace.status == WorkspaceStatus.ACTIVE, Workspace.host == self._host
                    )
                )
            ).all()
            running = set(
                (
                    await session.scalars(
                        select(Execution.id).where(
                            Execution.host == self._host, Execution.status.in_(_UNFINISHED)
                        )
                    )
                ).all()
            )
        kept = {container for row in rows for container in (row.holder_id, row.computer_id)}
        volumes = {name for row in rows for name in (row.workspace_volume, row.home_volume)}
        containers, owned_volumes = await self._docker.owned()
        for container_id, labels in containers:
            execution = labels.get(LABEL_EXECUTION)
            if (
                container_id in kept
                or (execution and UUID(execution) in running)
                or labels.get(LABEL_POOL) == self._pool.id
                or float(labels.get(LABEL_EXPIRES, 0)) > time.time()
            ):
                continue
            logger.info(
                "removing orphan container %s of chat %s", container_id, labels.get(LABEL_CHAT)
            )
            await self._docker.remove_container(container_id)
        for name, labels in owned_volumes:
            if name not in volumes and labels.get(LABEL_POOL) != self._pool.id:
                logger.info("removing orphan volume %s", name)
                await self._docker.remove_volume(name)

    async def _start_holder(
        self, row: Workspace, *, restore: bool, computer: Computer | None = None
    ) -> Active:
        """Start the holder; when restoring, Chromium of ``computer`` is closed meanwhile."""
        workspace_volume, home_volume = _volumes(row)
        name = holder_name(row.chat_id)
        await self._docker.remove_container(name)
        holder_id = await self._docker.start_container(
            name,
            holder_config(
                self._settings.execute,
                self._docker.labels(Role.HOLDER, row.chat_id),
                workspace_volume,
                home_volume,
            ),
        )
        restored_at = None
        if restore and row.checkpoint is not None:
            if computer is not None:
                await computer.call("browser.stop", timeout=_BROWSER_RESTART_TIMEOUT)
            for archive, directory in checkpoints.ARCHIVES.items():
                await self._docker.stream_in(
                    holder_id,
                    checkpoints.unpack_command(directory),
                    checkpoints.download(self._s3, self._bucket, row.checkpoint + archive),
                )
            if computer is not None:
                await computer.call("browser.start", timeout=_BROWSER_RESTART_TIMEOUT)
            restored_at = row.checkpoint_at
        await self._docker.check(holder_id, ["touch", MARKER])
        async with self._sessions.begin() as session:
            updated = await session.scalar(
                update(Workspace)
                .where(Workspace.chat_id == row.chat_id, Workspace.epoch == row.epoch)
                .values(holder_id=holder_id)
                .returning(Workspace.chat_id)
            )
        if updated is None:
            await self._docker.remove_container(holder_id)
            raise WorkspaceError("env_busy", "the workspace was activated elsewhere")
        return Active(row.chat_id, row.epoch, workspace_volume, home_volume, holder_id, restored_at)

    @traced("xavier.checkpoint", CHECKPOINT_DURATION)
    async def _checkpoint(self, active: Active, previous: str | None) -> bool:
        changed = await self._docker.run(
            active.holder_id,
            ["find", WORKSPACE_MOUNT, HOME_MOUNT, "-newer", MARKER, "-print", "-quit"],
            limit=4096,
        )
        if changed.exit_code == 0 and not changed.stdout.strip() and previous is not None:
            return False
        try:
            await self.ensure_room(active)
        except WorkspaceError:
            return False
        await self._docker.check(active.holder_id, ["touch", MARKER])
        next_number = checkpoints.number(previous) + 1 if previous else 1
        target = checkpoints.prefix(active.chat_id, next_number)
        size = 0
        for archive, directory in checkpoints.ARCHIVES.items():
            size += await checkpoints.upload(
                self._s3,
                self._bucket,
                target + archive,
                self._docker.stream_out(active.holder_id, checkpoints.pack_command(directory)),
            )
        async with self._sessions.begin() as session:
            stored = await session.scalar(
                update(Workspace)
                .where(
                    Workspace.chat_id == active.chat_id,
                    Workspace.epoch == active.epoch,
                    Workspace.status == WorkspaceStatus.ACTIVE,
                )
                .values(checkpoint=target, checkpoint_at=func.now())
                .returning(Workspace.chat_id)
            )
        if stored is None:
            await delete_prefix(self._blobs, target)
            return False
        CHECKPOINT_SIZE.record(size)
        stale = next_number - _KEPT_CHECKPOINTS
        if stale >= 1:
            await delete_prefix(self._blobs, checkpoints.prefix(active.chat_id, stale))
        return True

    async def _make_room(self, chat_id: UUID) -> None:
        async with self._sessions() as session:
            active = (
                await session.scalars(
                    select(Workspace)
                    .where(
                        Workspace.status == WorkspaceStatus.ACTIVE,
                        Workspace.host == self._host,
                        Workspace.chat_id != chat_id,
                    )
                    .order_by(Workspace.touched_at)
                )
            ).all()
        if len(active) < self._settings.worker.max_workspaces:
            return
        for candidate in active:
            if not await self._busy(candidate.chat_id) and await self.hibernate(candidate.chat_id):
                return
        raise WorkspaceError("host_full", "this worker has no room for another workspace")

    async def _busy(self, chat_id: UUID) -> bool:
        """Return whether executions, terminal jobs or user control keep the workspace."""
        async with self._sessions() as session:
            executions = await session.scalar(
                select(func.count())
                .select_from(Execution)
                .where(Execution.chat_id == chat_id, Execution.status.in_(_UNFINISHED))
            )
        if executions:
            return True
        lease = await self._leases.current(gui_key(chat_id))
        if lease is not None and lease.holder == USER:
            return True
        computer = self._computers.get(chat_id)
        if computer is None:
            return False
        try:
            state = await computer.call("ping", timeout=_PING_TIMEOUT)
        except ComputerError, TimeoutError:
            return False
        return bool(state.get("jobs"))

    async def _stop_computer(self, row: Workspace) -> None:
        """Close Chromium and remove the Computer, keeping the workspace active."""
        computer = self._computers.pop(row.chat_id, None)
        if computer is None and row.computer_id:
            computer = await self._pool.attach(row.computer_id)
        if computer is not None:
            await computer.destroy()
        if row.computer_id:
            await self._set_computer(row, None)

    async def _release(self, row: Workspace, status: WorkspaceStatus) -> None:
        await self._remove_containers(row)
        async with self._sessions.begin() as session:
            await session.execute(
                update(Workspace)
                .where(Workspace.chat_id == row.chat_id, Workspace.epoch == row.epoch)
                .values(
                    status=status,
                    host=None,
                    holder_id=None,
                    computer_id=None,
                    workspace_volume=None,
                    home_volume=None,
                )
            )

    async def _remove_containers(self, row: Workspace) -> None:
        computer = self._computers.pop(row.chat_id, None)
        if computer is not None:
            await computer.destroy()
        for container in (row.computer_id, row.holder_id, holder_name(row.chat_id)):
            if container:
                await self._docker.remove_container(container)
        for volume in (row.workspace_volume, row.home_volume):
            if volume:
                await self._docker.remove_volume(volume)

    async def _row(self, chat_id: UUID) -> Workspace | None:
        async with self._sessions() as session:
            return await session.get(Workspace, chat_id)


def _active(row: Workspace, holder_id: str) -> Active:
    workspace_volume, home_volume = _volumes(row)
    return Active(row.chat_id, row.epoch, workspace_volume, home_volume, holder_id)


def _volumes(row: Workspace) -> tuple[str, str]:
    """Return the volume names of an active workspace."""
    if row.workspace_volume is None or row.home_volume is None:
        raise ValueError(f"the active workspace of chat {row.chat_id} has no volumes")
    return row.workspace_volume, row.home_volume


async def session_row(sessions: Sessions, chat_id: UUID) -> Workspace:
    """Return the workspace row, which must exist."""
    async with sessions() as session:
        return await session.get_one(Workspace, chat_id)


def _content_type(path: str) -> str:
    return mimetypes.guess_type(path)[0] or "application/octet-stream"


async def mark_lost(sessions: Sessions, chat_id: UUID, epoch: int) -> bool:
    """Move a workspace whose host is gone to ``hibernated`` with its last checkpoint.

    Unfinished executions on that host become ``uncertain``. Returns whether this call moved it.
    """
    active = (
        Workspace.chat_id == chat_id,
        Workspace.epoch == epoch,
        Workspace.status == WorkspaceStatus.ACTIVE,
    )
    async with sessions.begin() as session:
        lost = (
            await session.execute(select(Workspace.host).where(*active).with_for_update())
        ).one_or_none()
        if lost is None:
            return False
        host = lost.host
        await session.execute(
            update(Workspace)
            .where(*active)
            .values(
                status=WorkspaceStatus.HIBERNATED,
                host=None,
                holder_id=None,
                computer_id=None,
                workspace_volume=None,
                home_volume=None,
            )
        )
        await session.execute(
            update(Execution)
            .where(
                Execution.chat_id == chat_id,
                Execution.host == host,
                Execution.status == ExecutionStatus.RUNNING,
            )
            .values(
                status=ExecutionStatus.UNCERTAIN,
                error="the worker running it was lost",
                finished_at=func.now(),
            )
        )
        await append_events(
            session, chat_id, [NewEvent("workspace", {"status": "lost", "host": host})]
        )
    return True
