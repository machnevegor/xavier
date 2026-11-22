"""API keys of the signed-in user."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import Field

from src.api.common import Input, ListResult, PageQuery, Result, Schema, SortDirection

# --- DATA MODEL --- #


class ApiKey(Schema):
    """This object represents an API key; its full value is returned only at creation."""

    id: UUID = Field(description="Unique API key identifier")
    name: str = Field(description="What the key is for")
    prefix: str = Field(description="Start of the key, `xv_` and 8 characters, to recognize it")
    created_at: datetime = Field(description="Date and time of API key creation")
    last_used_at: datetime | None = Field(
        description="_Optional_. Date and time of the last use, to a minute"
    )
    revoked_at: datetime | None = Field(description="_Optional_. Date and time of revocation")


# --- QUERY PARAMS --- #


class ApiKeysSort(StrEnum):
    """Fields API keys can be sorted by."""

    ID = "id"
    CREATED_AT = "created_at"
    NAME = "name"


class ListApiKeysInput(PageQuery):
    """This object represents the input used to list API keys."""

    sort: ApiKeysSort = Field(default=ApiKeysSort.CREATED_AT, description="_Optional_. Sort field")
    direction: SortDirection = Field(
        default=SortDirection.DESC,
        description="_Optional_. Sort direction. Can be either `asc` or `desc`",
    )


# --- INPUTS --- #


class CreateApiKeyInput(Input):
    """This object represents the input used to create the API key."""

    name: str = Field(min_length=1, max_length=100, description="What the key is for")


# --- ARGS --- #


class CreateApiKeyArgs(Input):
    """This object represents the arguments used to create the API key."""

    data: CreateApiKeyInput


# --- RESULTS --- #


class ListApiKeysResult(ListResult[ApiKey]):
    """This object represents the result when API keys are listed."""


class CreateApiKeyResult(Result[ApiKey]):
    """This object represents the result when the API key is created."""

    key: str = Field(
        description="Full API key, sent as `Authorization: Bearer <key>`. It is returned only "
        "here and cannot be read again"
    )


class DeleteApiKeyResult(Result[ApiKey]):
    """This object represents the result when the API key is revoked."""
