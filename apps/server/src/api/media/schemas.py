"""Images of a chat: its image files and the web images of agent answers."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import Field

from src.api.common import Input, ListResult, OffsetQuery, Schema, SortDirection

# --- DATA MODEL --- #


class MediaKind(StrEnum):
    """Origin of an image of a chat."""

    ARTIFACT = "artifact"
    EXTERNAL = "external"


class Media(Schema):
    """This object represents an image shown in a chat."""

    id: str = Field(
        description="Unique media identifier: the artifact id, or `<event id>:<index>` for a "
        "web image"
    )
    kind: MediaKind = Field(
        description="*artifact* for an image file of the chat, *external* for a web image an "
        "agent answer shows"
    )
    name: str = Field(description="File name, or the last part of the web address")
    content_type: str | None = Field(
        description="_Optional_. Media type; *null* until a web image is fetched"
    )
    size_bytes: int | None = Field(description="_Optional_. Size of an image file in bytes")
    url: str = Field(description="API path of the image, relative to the API prefix")
    source_url: str | None = Field(description="_Optional_. Web address of an external image")
    artifact_id: UUID | None = Field(description="_Optional_. Artifact of an image file")
    event_id: UUID | None = Field(description="_Optional_. Agent answer that shows a web image")
    created_at: datetime = Field(description="Date and time the image appeared")


# --- QUERY PARAMS --- #


class MediaFilter(Input):
    """This object represents the filter of the media list."""

    kind: list[MediaKind] | None = Field(
        default=None,
        alias="filter[kind]",
        description="_Optional_. Filter by one or more `kind` values",
    )


class MediaSort(StrEnum):
    """Sort field of the media list."""

    CREATED_AT = "created_at"


class ListMediaInput(MediaFilter, OffsetQuery):
    """This object represents the input used to list the images of a chat."""

    sort: MediaSort = Field(default=MediaSort.CREATED_AT, description="_Optional_. Sort field")
    direction: SortDirection = Field(
        default=SortDirection.DESC,
        description="_Optional_. Sort direction. Can be either `asc` or `desc`",
    )


# --- RESULTS --- #


class ListMediaResult(ListResult[Media]):
    """This object represents the result when the images of a chat are listed."""
