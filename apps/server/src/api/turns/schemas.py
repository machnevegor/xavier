"""Turns: agent passes over the accumulated input, with their usage."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import Field

from src.api.common import Input, ListResult, PageQuery, Result, Schema, SortDirection
from src.db import models as db

# --- DATA MODEL --- #


class Turn(Schema):
    """This object represents a turn: one agent pass over the accumulated input."""

    id: UUID = Field(description="Unique turn identifier")
    chat_id: UUID = Field(description="Unique chat identifier")
    status: db.TurnStatus = Field(description="Outcome of the turn")
    reason: str | None = Field(description="_Optional_. Why the turn ended other than *completed*")
    trigger: db.TurnTrigger = Field(
        description="*wakeup* and *event* turns were started by the agent in Proactive mode"
    )
    final_response: str | None = Field(description="_Optional_. Last answer of the agent")
    steps: int = Field(description="Model calls of the turn")
    input_tokens: int = Field(description="Input tokens")
    output_tokens: int = Field(description="Output tokens")
    cache_read_tokens: int = Field(description="Input tokens read from the cache")
    cost_usd: float = Field(description="Cost in USD")
    superseded_at: datetime | None = Field(
        description="_Optional_. Date and time a revision of earlier input replaced the turn"
    )
    started_at: datetime = Field(description="Date and time of turn start")
    finished_at: datetime | None = Field(description="_Optional_. Date and time of turn end")


# --- QUERY PARAMS --- #


class TurnsFilter(Input):
    """This object represents the filter of turns."""

    status: list[db.TurnStatus] | None = Field(
        default=None,
        alias="filter[status]",
        description="_Optional_. Filter by one or more `status` values",
    )
    trigger: list[db.TurnTrigger] | None = Field(
        default=None,
        alias="filter[trigger]",
        description="_Optional_. Filter by one or more `trigger` values",
    )
    include_superseded: bool = Field(
        default=False,
        alias="filter[include_superseded]",
        description="_Optional_. Also list turns a message revision replaced",
    )


class TurnsSort(StrEnum):
    """Fields turns can be sorted by."""

    ID = "id"
    STARTED_AT = "started_at"


class ListTurnsInput(TurnsFilter, PageQuery):
    """This object represents the input used to list turns."""

    sort: TurnsSort = Field(default=TurnsSort.ID, description="_Optional_. Sort field")
    direction: SortDirection = Field(
        default=SortDirection.ASC,
        description="_Optional_. Sort direction. Can be either `asc` or `desc`",
    )


# --- RESULTS --- #


class ListTurnsResult(ListResult[Turn]):
    """This object represents the result when turns are listed."""


class ReadTurnResult(Result[Turn]):
    """This object represents the result when the turn is read."""
