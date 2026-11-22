"""Binary objects in S3-compatible storage."""

from collections.abc import AsyncIterator, Sequence
from itertools import batched
from typing import Protocol, cast

import aioboto3
from aiobotocore.config import AioConfig
from aiobotocore.session import ClientCreatorContext
from botocore.exceptions import ClientError
from types_aiobotocore_s3.client import S3Client
from types_aiobotocore_s3.literals import BucketLocationConstraintType
from types_aiobotocore_s3.type_defs import ObjectIdentifierTypeDef

from src.config import S3Settings

_DELETE_BATCH = 1000
_NOT_FOUND = frozenset({"404", "NoSuchKey", "NotFound"})


class BlobNotFoundError(LookupError):
    """The requested key does not exist."""


class BlobStore(Protocol):
    """Binary objects addressed by key within one bucket."""

    async def put(
        self, key: str, data: bytes, *, content_type: str = "application/octet-stream"
    ) -> None:
        """Store ``data`` under ``key``, replacing any existing object."""
        ...

    async def get(self, key: str) -> bytes:
        """Return the object stored under ``key`` or raise `BlobNotFoundError`."""
        ...

    async def exists(self, key: str) -> bool:
        """Return whether ``key`` exists."""
        ...

    async def delete(self, keys: Sequence[str]) -> None:
        """Delete ``keys``; missing keys are ignored."""
        ...

    def list_keys(self, prefix: str) -> AsyncIterator[str]:
        """Yield every key that starts with ``prefix``."""
        ...


class S3Adapter:
    """`BlobStore` over an S3 bucket."""

    def __init__(self, client: S3Client, bucket: str) -> None:
        self._client = client
        self._bucket = bucket

    async def put(
        self, key: str, data: bytes, *, content_type: str = "application/octet-stream"
    ) -> None:
        """Store ``data`` under ``key``, replacing any existing object."""
        await self._client.put_object(
            Bucket=self._bucket, Key=key, Body=data, ContentType=content_type
        )

    async def get(self, key: str) -> bytes:
        """Return the object stored under ``key`` or raise `BlobNotFoundError`."""
        try:
            response = await self._client.get_object(Bucket=self._bucket, Key=key)
        except ClientError as error:
            if _error_code(error) in _NOT_FOUND:
                raise BlobNotFoundError(key) from error
            raise
        async with response["Body"] as body:
            return await body.read()

    async def exists(self, key: str) -> bool:
        """Return whether ``key`` exists."""
        try:
            await self._client.head_object(Bucket=self._bucket, Key=key)
        except ClientError as error:
            if _error_code(error) in _NOT_FOUND:
                return False
            raise
        return True

    async def delete(self, keys: Sequence[str]) -> None:
        """Delete ``keys``; missing keys are ignored.

        Raises
        ------
        RuntimeError
            If the storage rejected the deletion of any key.
        """
        for batch in batched(keys, _DELETE_BATCH, strict=False):
            objects: list[ObjectIdentifierTypeDef] = [{"Key": key} for key in batch]
            response = await self._client.delete_objects(
                Bucket=self._bucket, Delete={"Objects": objects, "Quiet": True}
            )
            if errors := response.get("Errors"):
                failed = ", ".join(f"{e.get('Key')}: {e.get('Code')}" for e in errors)
                raise RuntimeError(f"failed to delete objects from {self._bucket}: {failed}")

    async def list_keys(self, prefix: str) -> AsyncIterator[str]:
        """Yield every key that starts with ``prefix``."""
        paginator = self._client.get_paginator("list_objects_v2")
        async for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
            for item in page.get("Contents", []):
                yield item["Key"]


async def delete_prefix(blobs: BlobStore, prefix: str) -> None:
    """Delete every object whose key starts with ``prefix``."""
    await blobs.delete([key async for key in blobs.list_keys(prefix)])


def open_s3_client(settings: S3Settings) -> ClientCreatorContext[S3Client]:
    """Return an async context manager that yields a configured S3 client."""
    return aioboto3.Session().client(
        "s3",
        endpoint_url=settings.endpoint_url,
        region_name=settings.region,
        aws_access_key_id=settings.access_key_id.get_secret_value(),
        aws_secret_access_key=settings.secret_access_key.get_secret_value(),
        config=AioConfig(
            s3={"addressing_style": settings.addressing_style}, retries={"mode": "standard"}
        ),
    )


async def ensure_buckets(client: S3Client, settings: S3Settings) -> None:
    """Create the buckets if missing and apply the Temporal bucket expiration rule."""
    for bucket in (settings.artifacts_bucket, settings.temporal_bucket):
        try:
            await client.head_bucket(Bucket=bucket)
        except ClientError as error:
            if _error_code(error) not in _NOT_FOUND:
                raise
            if settings.region == "us-east-1":
                await client.create_bucket(Bucket=bucket)
            else:
                region = cast("BucketLocationConstraintType", settings.region)
                await client.create_bucket(
                    Bucket=bucket, CreateBucketConfiguration={"LocationConstraint": region}
                )
    await client.put_bucket_lifecycle_configuration(
        Bucket=settings.temporal_bucket,
        LifecycleConfiguration={
            "Rules": [
                {
                    "ID": "expire-temporal-payloads",
                    "Filter": {"Prefix": ""},
                    "Status": "Enabled",
                    "Expiration": {"Days": settings.temporal_payload_ttl_days},
                }
            ]
        },
    )


def _error_code(error: ClientError) -> str:
    return str(error.response.get("Error", {}).get("Code", ""))
