import hashlib
import json
import uuid
from collections.abc import AsyncIterator, Sequence
from pathlib import Path

import pytest

from src.agent import snapshots
from src.storage.blobs import BlobNotFoundError

CHAT = uuid.uuid4()
TURN = uuid.uuid4()


class MemoryBlobs:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.puts: list[str] = []

    async def put(
        self, key: str, data: bytes, *, content_type: str = "application/octet-stream"
    ) -> None:
        del content_type
        self.objects[key] = data
        self.puts.append(key)

    async def get(self, key: str) -> bytes:
        if key not in self.objects:
            raise BlobNotFoundError(key)
        return self.objects[key]

    async def exists(self, key: str) -> bool:
        return key in self.objects

    async def delete(self, keys: Sequence[str]) -> None:
        for key in keys:
            self.objects.pop(key, None)

    async def list_keys(self, prefix: str) -> AsyncIterator[str]:
        for key in list(self.objects):
            if key.startswith(prefix):
                yield key


def _home(root: Path) -> Path:
    (root / "sessions" / "cwd" / "s1").mkdir(parents=True)
    (root / "sessions" / "cwd" / "s1" / "session.v4.jsonl").write_text('{"type":"session"}\n')
    (root / "sessions" / "cwd" / "s1" / "session.lock").write_text("pid")
    (root / "attachments").mkdir()
    (root / "attachments" / "a.png").write_bytes(b"png")
    (root / "xavier.patch.yml").write_text("[]")
    return root


async def test_snapshot_round_trip_keeps_sessions_and_attachments(tmp_path: Path) -> None:
    blobs = MemoryBlobs()
    key = await snapshots.upload(blobs, CHAT, TURN, 1, _home(tmp_path / "a"))
    assert key is not None
    target = tmp_path / "b"

    await snapshots.restore(blobs, CHAT, key, target)

    restored = sorted(p.relative_to(target).as_posix() for p in target.rglob("*") if p.is_file())
    assert restored == ["attachments/a.png", "sessions/cwd/s1/session.v4.jsonl"]
    assert (target / "attachments" / "a.png").read_bytes() == b"png"
    assert key == snapshots.manifest_key(CHAT, TURN, 1)


async def test_home_without_a_persisted_session_is_not_snapshotted(tmp_path: Path) -> None:
    blobs = MemoryBlobs()
    session = tmp_path / "sessions" / "cwd" / "s1"
    session.mkdir(parents=True)
    (session / "session.lock").write_text("pid")

    assert await snapshots.upload(blobs, CHAT, TURN, 1, tmp_path) is None
    assert blobs.puts == []


async def test_unchanged_files_are_not_uploaded_again(tmp_path: Path) -> None:
    blobs = MemoryBlobs()
    home = _home(tmp_path)
    await snapshots.upload(blobs, CHAT, TURN, 1, home)
    blobs.puts.clear()

    await snapshots.upload(blobs, CHAT, TURN, 2, home)

    assert blobs.puts == [snapshots.manifest_key(CHAT, TURN, 2)]


async def test_prune_keeps_referenced_manifests_and_their_blobs(tmp_path: Path) -> None:
    blobs = MemoryBlobs()
    home = _home(tmp_path)
    file = home / "sessions" / "cwd" / "s1" / "session.v4.jsonl"
    turns = sorted(uuid.uuid7() for _ in range(5))
    keys: list[str] = []
    for index, turn in enumerate(turns):
        file.write_text(f"turn {index}\n")
        key = await snapshots.upload(blobs, CHAT, turn, 1, home)
        assert key is not None
        keys.append(key)

    kept = {keys[0], keys[-1]}
    await snapshots.prune(blobs, CHAT, kept)

    manifests = {k for k in blobs.objects if "/manifests/" in k}
    assert manifests == kept
    for key in manifests:
        target = tmp_path / f"restore-{key.rsplit('/', 1)[1]}"
        await snapshots.restore(blobs, CHAT, key, target)
    stale = hashlib.sha256(b"turn 1\n").hexdigest()
    assert snapshots.blob_key(CHAT, stale) not in blobs.objects


@pytest.mark.parametrize("path", ["../escape", "/etc/passwd", "settings.yaml", "sessions/../x"])
async def test_manifest_paths_outside_the_snapshot_are_rejected(tmp_path: Path, path: str) -> None:
    blobs = MemoryBlobs()
    blobs.objects["m"] = json.dumps({path: "digest"}).encode()

    with pytest.raises(ValueError, match="invalid snapshot path"):
        await snapshots.restore(blobs, CHAT, "m", tmp_path)
