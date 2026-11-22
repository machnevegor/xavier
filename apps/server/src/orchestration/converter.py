"""Temporal data converter."""

import dataclasses

from temporalio.contrib.aws.s3driver import S3StorageDriver
from temporalio.contrib.aws.s3driver.aioboto3 import new_aioboto3_client
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.converter import DataConverter, ExternalStorage
from types_aiobotocore_s3.client import S3Client


def create_data_converter(s3: S3Client, bucket: str, payload_size_threshold: int) -> DataConverter:
    """Return the Pydantic converter that offloads large payloads to ``bucket``."""
    storage = ExternalStorage(
        drivers=[S3StorageDriver(client=new_aioboto3_client(s3), bucket=bucket)],
        payload_size_threshold=payload_size_threshold,
    )
    return dataclasses.replace(pydantic_data_converter, external_storage=storage)
