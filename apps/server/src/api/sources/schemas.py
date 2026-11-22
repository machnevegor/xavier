"""Observe sources and their versions."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import Field, JsonValue

from src.api.common import Input, ListResult, PageQuery, Result, Schema, SortDirection
from src.db import models as db
from src.observe.spec import SourceSpec

# --- DATA MODEL --- #


class Source(Schema):
    """This object represents an Observe source."""

    id: UUID = Field(description="Unique source identifier")
    name: str = Field(description="Name of the source")
    status: db.SourceStatus = Field(description="Whether it is a draft, published or paused")
    pause_reason: db.SourcePauseReason | None = Field(description="Why polling stopped")
    spec: JsonValue = Field(description="Specification that is polled once published")
    draft: JsonValue | None = Field(description="Revision awaiting publication")
    widening: list[str] = Field(
        default=[],
        description="What publishing allows beyond the polled specification: hosts, credential, "
        "limits",
    )
    chat_id: UUID | None = Field(description="Unique identifier of the chat whose agent drafted it")
    last_polled_at: datetime | None = Field(description="Date and time of the last poll")
    last_success_at: datetime | None = Field(
        description="Date and time of the last successful poll"
    )
    last_error: str | None = Field(description="Error of the last failed poll")
    created_at: datetime = Field(description="Date and time of source creation")
    updated_at: datetime = Field(description="Date and time of last source update")
    updated_by: UUID | None = Field(description="User who last changed the source")


class Version(Schema):
    """This object represents a distinct observed value of a resource."""

    id: UUID = Field(description="Unique version identifier")
    resource_key: str = Field(
        description='`""` for the source URL, `<rule>:<value>` for a child resource'
    )
    url: str = Field(description="URL that was fetched")
    gone: bool = Field(description="_True_, if the resource was gone")
    content_type: str = Field(description="Media type of the observed value")
    size_bytes: int = Field(description="Size of the observed value in bytes")
    structure: JsonValue | None = Field(description="Structural summary of the value")
    schema_changes: JsonValue = Field(description="Structure changes from the previous version")
    changes: JsonValue = Field(description="Up to 50 value changes from the previous version")
    change_count: int = Field(description="Number of value changes from the previous version")
    created_at: datetime = Field(description="Date and time of version creation")


class VersionValue(Version):
    """This object represents a version with its observed value."""

    value: JsonValue = Field(description="Normalized observed value; *null* when gone")


class Diff(Schema):
    """This object represents the changes between two versions of one resource."""

    from_version: UUID = Field(description="Unique identifier of the earlier version")
    to_version: UUID = Field(description="Unique identifier of the later version")
    resource_key: str = Field(description="Resource both versions belong to")
    change_count: int = Field(description="Number of value changes")
    changes: list[JsonValue] = Field(description="Up to 50 value changes")
    schema_changes: list[JsonValue] = Field(description="Structure changes")


# --- QUERY PARAMS --- #


class SourcesFilter(Input):
    """Which sources to list."""

    status: list[db.SourceStatus] | None = Field(
        default=None,
        alias="filter[status]",
        description="_Optional_. Filter by one or more `status` values",
    )
    chat_id: list[UUID] | None = Field(
        default=None,
        alias="filter[chat_id]",
        description="_Optional_. Filter by one or more `chat_id` values",
    )


class SourcesSort(StrEnum):
    """Fields sources can be sorted by."""

    ID = "id"
    NAME = "name"
    CREATED_AT = "created_at"
    UPDATED_AT = "updated_at"


class ListSourcesInput(SourcesFilter, PageQuery):
    """This object represents the input used to list sources."""

    sort: SourcesSort = Field(default=SourcesSort.CREATED_AT, description="_Optional_. Sort field")
    direction: SortDirection = Field(
        default=SortDirection.ASC,
        description="_Optional_. Sort direction. Can be either `asc` or `desc`",
    )


class VersionsFilter(Input):
    """Which versions to list."""

    resource_key: str | None = Field(
        default=None,
        alias="filter[resource_key]",
        description='_Optional_. Filter by `resource_key`; `""` is the source URL',
    )


class VersionsSort(StrEnum):
    """Fields versions can be sorted by."""

    ID = "id"
    CREATED_AT = "created_at"


class ListVersionsInput(VersionsFilter, PageQuery):
    """This object represents the input used to list versions."""

    sort: VersionsSort = Field(default=VersionsSort.ID, description="_Optional_. Sort field")
    direction: SortDirection = Field(
        default=SortDirection.DESC,
        description="_Optional_. Sort direction. Can be either `asc` or `desc`",
    )


class DiffVersionsInput(Input):
    """This object represents the input used to compare two versions."""

    from_version: UUID = Field(alias="from", description="Unique identifier of the earlier version")
    to_version: UUID = Field(alias="to", description="Unique identifier of the later version")


# --- INPUTS --- #


class CreateSourceInput(Input):
    """This object represents the input used to create the source as a draft."""

    name: str = Field(min_length=1, max_length=100, description="Name of the source")
    spec: SourceSpec = Field(description="Specification that is polled once published")


class ReplaceSourceInput(Input):
    """This object represents the input used to replace the source specification."""

    name: str = Field(min_length=1, max_length=100, description="Name of the source")
    spec: SourceSpec = Field(
        description="Specification; a polled source uses it from its next poll"
    )


# --- ARGS --- #


class CreateSourceArgs(Input):
    """This object represents the arguments used to create the source."""

    data: CreateSourceInput


class ReplaceSourceArgs(Input):
    """This object represents the arguments used to replace the source."""

    data: ReplaceSourceInput


# --- RESULTS --- #


class CreateSourceResult(Result[Source]):
    """This object represents the result when the source is created."""


class ListSourcesResult(ListResult[Source]):
    """This object represents the result when sources are listed."""


class ReadSourceResult(Result[Source]):
    """This object represents the result when the source is read."""


class ReplaceSourceResult(Result[Source]):
    """This object represents the result when the source is replaced."""


class DeleteSourceResult(Result[Source]):
    """This object represents the result when the source is deleted."""


class PublishSourceResult(Result[Source]):
    """This object represents the result when the source is published."""


class PauseSourceResult(Result[Source]):
    """This object represents the result when the source is paused."""


class ResumeSourceResult(Result[Source]):
    """This object represents the result when the source is resumed."""


class CheckSourceResult(Result[Source]):
    """This object represents the result when a poll of the source is requested."""


class ListVersionsResult(ListResult[Version]):
    """This object represents the result when versions are listed."""


class ReadVersionResult(Result[VersionValue]):
    """This object represents the result when the version is read."""


class DiffVersionsResult(Result[Diff]):
    """This object represents the result when two versions are compared."""
