from collections.abc import AsyncIterator

import pytest
from pydantic_settings import InitSettingsSource
from types_aiobotocore_s3.client import S3Client

from src.config import Settings
from src.storage.blobs import ensure_buckets, open_s3_client

# Matches docker-compose.test.yaml; deliberately ignores settings.yaml and XAVIER_* variables.
TEST_STACK = {
    "api": {},
    "worker": {"id": "test-worker", "internal_token": "test-internal"},
    "agent": {
        "providers": {"test": {"base_url": "http://127.0.0.1:9/v1", "api_key": "test-key"}},
        "models": [
            {
                "id": "test",
                "provider": "test",
                "provider_model": "test/model",
                "pricing_per_mtok": {"input": 1.0, "output": 2.0},
            }
        ],
        "default_model": "test",
    },
    "database": {"port": 35432, "password": "test"},
    "s3": {
        "endpoint_url": "http://127.0.0.1:19000",
        "access_key_id": "test",
        "secret_access_key": "testsecret",
    },
    "temporal": {"address": "127.0.0.1:17233"},
    "observe": {"credentials_key": "vBEIpEgikO_ICioUfkcoQ2wQc9mtSBVhuxtZW05nJqM="},
}


@pytest.fixture(scope="session")
def settings() -> Settings:
    return Settings(_build_sources=((InitSettingsSource(Settings, init_kwargs=TEST_STACK),), {}))


@pytest.fixture
async def s3(settings: Settings) -> AsyncIterator[S3Client]:
    async with open_s3_client(settings.s3) as client:
        await ensure_buckets(client, settings.s3)
        yield client
