"""Files: uploads and the outputs of tools and runs."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import Field

from src.api.common import Input, ListResult, PageQuery, Result, Schema, SortDirection

# --- DATA MODEL --- #


class Artifact(Schema):
    """This object represents a stored file."""

    id: UUID = Field(description="Unique artifact identifier")
    chat_id: UUID | None = Field(
        description="_Optional_. Chat the artifact belongs to; *null* for a file outside chats"
    )
    name: str = Field(description="File name")
    content_type: str = Field(description="Media type")
    size_bytes: int = Field(description="Size in bytes")
    sha256: str = Field(description="SHA-256 of the content, hexadecimal")
    source: str = Field(
        description="*upload*, *export*, *output*, *screenshot*, *result* or *embed*"
    )
    created_at: datetime = Field(description="Date and time of artifact creation")


# --- QUERY PARAMS --- #


class ArtifactKind(StrEnum):
    """Kind of an artifact by its media type."""

    IMAGE = "image"
    FILE = "file"


class ArtifactsFilter(Input):
    """This object represents the filter of the artifact list."""

    chat_id: list[UUID] | None = Field(
        default=None,
        alias="filter[chat_id]",
        description="_Optional_. Filter by one or more `chat_id` values",
    )
    kind: ArtifactKind | None = Field(
        default=None,
        alias="filter[kind]",
        description="_Optional_. *image* for PNG, JPEG, GIF and WebP images, *file* for "
        "everything else",
    )


class ArtifactsSort(StrEnum):
    """Sort field of the artifact list."""

    ID = "id"
    CREATED_AT = "created_at"
    NAME = "name"
    SIZE_BYTES = "size_bytes"


class ListArtifactsInput(ArtifactsFilter, PageQuery):
    """This object represents the input used to list artifacts."""

    sort: ArtifactsSort = Field(
        default=ArtifactsSort.CREATED_AT, description="_Optional_. Sort field"
    )
    direction: SortDirection = Field(
        default=SortDirection.DESC,
        description="_Optional_. Sort direction. Can be either `asc` or `desc`",
    )


# --- RESULTS --- #


class CreateArtifactResult(Result[Artifact]):
    """This object represents the result when the artifact is uploaded."""


class ListArtifactsResult(ListResult[Artifact]):
    """This object represents the result when artifacts are listed."""


class ReadArtifactResult(Result[Artifact]):
    """This object represents the result when the artifact is read."""
