"""Harness session snapshots in S3.

A snapshot is every file under ``sessions/`` and ``attachments/`` of a Harness home except lock
files. Files are content-addressed blobs; a manifest maps relative paths to a blob, or to a list
of chunk blobs for append-only logs (``*.jsonl``). Chunks end at line breaks after `CHUNK_BYTES`
counted from the previous end, so the checkpoints of successive turns share every chunk but the
last and a checkpoint per turn costs about the size of the turn.
"""

import asyncio
import contextlib
import hashlib
import json
import os
from collections.abc import Collection
from pathlib import Path
from uuid import UUID

from src.storage.blobs import BlobStore

PARTS = ("sessions", "attachments")
CHUNK_BYTES = 1024 * 1024
CACHE_BYTES = 1024**3
"""Size the worker-local blob cache is trimmed to."""

type Manifest = dict[str, str | list[str]]


def blob_key(chat_id: UUID, digest: str) -> str:
    """Return the key of a snapshot blob."""
    return f"snapshots/{chat_id}/blobs/{digest}"


def manifest_key(chat_id: UUID, turn_id: UUID, attempt: int) -> str:
    """Return the key of the manifest written by one turn attempt."""
    return f"snapshots/{chat_id}/manifests/{turn_id}-{attempt}.json"


def collect(home: Path) -> dict[str, bytes]:
    """Return the snapshot files of ``home`` by relative POSIX path."""
    files: dict[str, bytes] = {}
    for part in PARTS:
        root = home / part
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if path.is_file() and path.suffix != ".lock":
                files[path.relative_to(home).as_posix()] = path.read_bytes()
    return files


def chunks(data: bytes) -> list[bytes]:
    """Split an append-only log into chunks that end at a line break after `CHUNK_BYTES`."""
    parts: list[bytes] = []
    start = 0
    while len(data) - start > CHUNK_BYTES:
        end = data.find(b"\n", start + CHUNK_BYTES)
        if end < 0:
            break
        parts.append(data[start : end + 1])
        start = end + 1
    parts.append(data[start:])
    return parts


def _digests(manifest: Manifest) -> set[str]:
    return {
        digest
        for value in manifest.values()
        for digest in ([value] if isinstance(value, str) else value)
    }


class BlobCache:
    """Snapshot blobs kept on this worker, so a chat's next turn restores without downloads."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._added = 0

    def get(self, digest: str) -> bytes | None:
        """Return the cached blob, or ``None``."""
        path = self._root / digest
        try:
            data = path.read_bytes()
        except OSError:
            return None
        if hashlib.sha256(data).hexdigest() != digest:
            path.unlink(missing_ok=True)
            return None
        with contextlib.suppress(OSError):
            os.utime(path)
        return data

    def put(self, digest: str, data: bytes) -> None:
        """Cache a blob, trimming the cache when it grew by a tenth of its size."""
        self._root.mkdir(parents=True, exist_ok=True)
        path = self._root / digest
        if path.exists():
            return
        temporary = path.with_suffix(".tmp")
        temporary.write_bytes(data)
        temporary.replace(path)
        self._added += len(data)
        if self._added > CACHE_BYTES // 10:
            self._added = 0
            self.trim()

    def trim(self) -> None:
        """Delete the least recently used blobs beyond `CACHE_BYTES`."""
        entries = []
        for path in self._root.iterdir():
            with contextlib.suppress(OSError):
                stat = path.stat()
                entries.append((stat.st_mtime, stat.st_size, path))
        total = sum(size for _time, size, _path in entries)
        for _time, size, path in sorted(entries):
            if total <= CACHE_BYTES:
                break
            path.unlink(missing_ok=True)
            total -= size


async def upload(  # noqa: PLR0913 - the snapshot's identity and stores
    blobs: BlobStore,
    chat_id: UUID,
    turn_id: UUID,
    attempt: int,
    home: Path,
    *,
    cache: BlobCache | None = None,
) -> str | None:
    """Upload the snapshot of ``home`` and return the manifest key.

    Return ``None`` without uploading when the runtime persisted no session, as when a turn
    fails before its first step: such a snapshot could not be resumed.
    """
    files = await asyncio.to_thread(collect, home)
    if not any(path.startswith("sessions/") for path in files):
        return None
    manifest: Manifest = {}
    contents: dict[str, bytes] = {}
    for path, data in files.items():
        parts = chunks(data) if path.endswith(".jsonl") else [data]
        digests = [hashlib.sha256(part).hexdigest() for part in parts]
        contents |= dict(zip(digests, parts, strict=True))
        manifest[path] = digests if path.endswith(".jsonl") else digests[0]

    async def put(digest: str) -> None:
        key = blob_key(chat_id, digest)
        if not await blobs.exists(key):
            await blobs.put(key, contents[digest])
        if cache is not None:
            await asyncio.to_thread(cache.put, digest, contents[digest])

    await asyncio.gather(*(put(digest) for digest in contents))
    key = manifest_key(chat_id, turn_id, attempt)
    await blobs.put(
        key, json.dumps(manifest, sort_keys=True).encode(), content_type="application/json"
    )
    return key


async def restore(
    blobs: BlobStore, chat_id: UUID, key: str, home: Path, *, cache: BlobCache | None = None
) -> None:
    """Write the snapshot ``key`` into ``home``.

    Raises
    ------
    ValueError
        If a manifest path leaves the snapshot parts.
    """
    manifest: Manifest = json.loads(await blobs.get(key))
    for path in manifest:
        relative = Path(path)
        if relative.is_absolute() or ".." in relative.parts or relative.parts[0] not in PARTS:
            raise ValueError(f"invalid snapshot path {path!r}")

    async def fetch(digest: str) -> tuple[str, bytes]:
        if cache is not None:
            cached = await asyncio.to_thread(cache.get, digest)
            if cached is not None:
                return digest, cached
        data = await blobs.get(blob_key(chat_id, digest))
        if cache is not None:
            await asyncio.to_thread(cache.put, digest, data)
        return digest, data

    contents = dict(await asyncio.gather(*(fetch(d) for d in _digests(manifest))))
    for path, value in manifest.items():
        target = home / path
        target.parent.mkdir(parents=True, exist_ok=True)
        digests = [value] if isinstance(value, str) else value
        target.write_bytes(b"".join(contents[digest] for digest in digests))


async def prune(blobs: BlobStore, chat_id: UUID, referenced: Collection[str]) -> None:
    """Delete manifests no turn or chat refers to and the blobs only they use.

    Call it only while no turn of the chat can upload, since new blobs precede their manifest.
    """
    prefix = f"snapshots/{chat_id}/manifests/"
    manifests = [key async for key in blobs.list_keys(prefix)]
    kept = set(referenced)
    stale = [key for key in manifests if key not in kept]
    if not stale:
        return
    used: set[str] = set()
    for key in kept & set(manifests):
        used |= {blob_key(chat_id, d) for d in _digests(json.loads(await blobs.get(key)))}
    unused = [
        key async for key in blobs.list_keys(f"snapshots/{chat_id}/blobs/") if key not in used
    ]
    await blobs.delete([*stale, *unused])
