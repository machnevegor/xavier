import uuid

import pytest
from types_aiobotocore_s3.client import S3Client

from src.config import Settings
from src.storage.blobs import BlobNotFoundError, S3Adapter, ensure_buckets

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


async def test_blob_lifecycle(s3: S3Client, settings: Settings) -> None:
    store = S3Adapter(s3, settings.s3.artifacts_bucket)
    prefix = f"tests/{uuid.uuid4().hex}/"
    keys = [f"{prefix}{i}" for i in range(3)]

    for i, key in enumerate(keys):
        await store.put(key, f"value-{i}".encode(), content_type="text/plain")

    assert await store.get(keys[1]) == b"value-1"
    assert await store.exists(keys[0])
    assert sorted([key async for key in store.list_keys(prefix)]) == keys

    await store.delete([*keys, f"{prefix}missing"])

    assert not await store.exists(keys[0])
    assert [key async for key in store.list_keys(prefix)] == []
    with pytest.raises(BlobNotFoundError):
        await store.get(keys[0])


async def test_ensure_buckets_is_idempotent(s3: S3Client, settings: Settings) -> None:
    await ensure_buckets(s3, settings.s3)
    await ensure_buckets(s3, settings.s3)

    lifecycle = await s3.get_bucket_lifecycle_configuration(Bucket=settings.s3.temporal_bucket)
    [rule] = lifecycle["Rules"]
    assert rule.get("Status") == "Enabled"
    assert rule.get("Expiration", {}).get("Days") == settings.s3.temporal_payload_ttl_days
