import pytest
from pydantic import BaseModel
from types_aiobotocore_s3.client import S3Client

from src.config import Settings
from src.orchestration.client import connect_temporal
from src.orchestration.converter import create_data_converter

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


class Blob(BaseModel):
    data: str


async def test_large_payloads_are_offloaded_to_s3(s3: S3Client, settings: Settings) -> None:
    threshold = settings.temporal.payload_size_threshold
    converter = create_data_converter(s3, settings.s3.temporal_bucket, threshold)
    value = Blob(data="x" * (threshold + 1024))

    [payload] = await converter.encode([value])
    [decoded] = await converter.decode([payload], [Blob])

    assert payload.ByteSize() < 4096
    assert decoded == value


async def test_small_payloads_stay_inline(s3: S3Client, settings: Settings) -> None:
    converter = create_data_converter(s3, settings.s3.temporal_bucket, 1024)

    [payload] = await converter.encode([Blob(data="small")])

    assert b"small" in payload.data


async def test_client_connects_to_namespace(s3: S3Client, settings: Settings) -> None:
    client = await connect_temporal(settings, s3)

    assert await client.service_client.check_health()
    assert client.namespace == settings.temporal.namespace
