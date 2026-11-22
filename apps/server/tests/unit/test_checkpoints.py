"""Workspace checkpoint transfer."""

import asyncio
from typing import Self, cast

from aiobotocore.response import StreamingBody
from types_aiobotocore_s3.client import S3Client

from src.workspace import checkpoints


class RawResponse:
    """The aiohttp response aiobotocore wraps; entering it returns the raw response."""

    def __init__(self, data: bytes) -> None:
        self.content = asyncio.StreamReader()
        self.content.feed_data(data)
        self.content.feed_eof()
        self.closed = False

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        self.closed = True


class FakeS3:
    def __init__(self, raw: RawResponse, size: int) -> None:
        self._body = StreamingBody(raw, str(size))  # ty: ignore[invalid-argument-type]

    async def get_object(self, *, Bucket: str, Key: str) -> dict[str, StreamingBody]:  # noqa: N803
        del Bucket, Key
        return {"Body": self._body}


async def test_download_yields_the_object_and_closes_the_response() -> None:
    data = b"x" * (3 * 1024 * 1024 + 5)
    raw = RawResponse(data)
    s3 = cast(S3Client, FakeS3(raw, len(data)))

    chunks = [chunk async for chunk in checkpoints.download(s3, "bucket", "key")]

    assert b"".join(chunks) == data
    assert raw.closed
