"""Workspace checkpoints: ``tar --zstd`` archives streamed between the holder and S3."""

from collections.abc import AsyncIterable, AsyncIterator
from uuid import UUID

from types_aiobotocore_s3.client import S3Client
from types_aiobotocore_s3.type_defs import CompletedPartTypeDef

from src.workspace.volumes import HOME_MOUNT, WORKSPACE_MOUNT

PART_BYTES = 8 * 1024 * 1024
"""Multipart part size; S3 requires at least 5 MiB for every part but the last."""
ARCHIVES = {"workspace.tar.zst": WORKSPACE_MOUNT, "home.tar.zst": HOME_MOUNT}
"""Archive name in a checkpoint and the directory it holds."""
_EXCLUDED = ("./.cache", "Code Cache", "GPUCache")
"""Caches are not kept: ``~/.cache`` holds the Chromium HTTP cache, the rest live in its profile."""


def prefix(chat_id: UUID, number: int) -> str:
    """Return the S3 prefix of checkpoint ``number``."""
    return f"workspaces/{chat_id}/checkpoints/{number}/"


def number(checkpoint: str) -> int:
    """Return the number of a checkpoint prefix."""
    return int(checkpoint.rstrip("/").rsplit("/", 1)[1])


def pack_command(directory: str) -> list[str]:
    """Return the holder command that writes ``directory`` as a zstd tar to stdout."""
    return ["tar", "--zstd", "-c", "-C", directory, *(f"--exclude={e}" for e in _EXCLUDED), "."]


def unpack_command(directory: str) -> list[str]:
    """Return the holder command that unpacks a zstd tar from stdin into ``directory``."""
    return ["tar", "--zstd", "-x", "-C", directory]


async def upload(s3: S3Client, bucket: str, key: str, chunks: AsyncIterable[bytes]) -> int:
    """Upload ``chunks`` as one object in parts; return its size. Aborts on failure."""
    upload_id = (await s3.create_multipart_upload(Bucket=bucket, Key=key))["UploadId"]
    parts: list[CompletedPartTypeDef] = []
    buffer = bytearray()
    size = 0

    async def flush() -> None:
        part = len(parts) + 1
        response = await s3.upload_part(
            Bucket=bucket, Key=key, UploadId=upload_id, PartNumber=part, Body=bytes(buffer)
        )
        parts.append({"ETag": response["ETag"], "PartNumber": part})
        buffer.clear()

    try:
        async for chunk in chunks:
            buffer.extend(chunk)
            size += len(chunk)
            if len(buffer) >= PART_BYTES:
                await flush()
        if buffer or not parts:
            await flush()
        await s3.complete_multipart_upload(
            Bucket=bucket,
            Key=key,
            UploadId=upload_id,
            MultipartUpload={"Parts": parts},
        )
    except BaseException:
        await s3.abort_multipart_upload(Bucket=bucket, Key=key, UploadId=upload_id)
        raise
    return size


async def download(s3: S3Client, bucket: str, key: str) -> AsyncIterator[bytes]:
    """Yield the object in chunks."""
    body = (await s3.get_object(Bucket=bucket, Key=key))["Body"]
    # `async with body as x` binds the raw aiohttp response, which has no `iter_chunks`.
    async with body:
        async for chunk in body.iter_chunks(1024 * 1024):
            yield chunk
