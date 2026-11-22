"""The chat feed."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import Field, JsonValue

from src.api.common import Input, ListResult, PageQuery, Schema, SortDirection

# --- DATA MODEL --- #


class Event(Schema):
    """This object represents an event: an entry of the chat feed.

    Kinds: `chat` (status), `turn`, `assistant` (agent text), `action` (tool calls, decisions
    and results), `question`, `wakeup`, `workspace`.
    """

    id: UUID = Field(description="Unique event identifier; events are ordered by it")
    chat_id: UUID = Field(description="Unique chat identifier")
    turn_id: UUID | None = Field(description="_Optional_. Unique turn identifier")
    kind: str = Field(description="Kind of the entry")
    data: JsonValue = Field(description="Content of the entry; its shape depends on `kind`")
    created_at: datetime = Field(description="Date and time of event creation")


# --- QUERY PARAMS --- #


class EventsFilter(Input):
    """This object represents the filter of events."""

    turn_id: list[UUID] | None = Field(
        default=None,
        alias="filter[turn_id]",
        description="_Optional_. Filter by one or more `turn_id` values",
    )


class EventsSort(StrEnum):
    """Fields events can be sorted by."""

    ID = "id"
    CREATED_AT = "created_at"


class ListEventsInput(EventsFilter, PageQuery):
    """This object represents the input used to list events."""

    sort: EventsSort = Field(default=EventsSort.ID, description="_Optional_. Sort field")
    direction: SortDirection = Field(
        default=SortDirection.ASC,
        description="_Optional_. Sort direction. Can be either `asc` or `desc`",
    )


# --- RESULTS --- #


class ListEventsResult(ListResult[Event]):
    """This object represents the result when events are listed."""
