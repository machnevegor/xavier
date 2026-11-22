"""Wakeups the agent scheduled in Proactive mode."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import Field

from src.api.common import Input, ListResult, PageQuery, Result, Schema, SortDirection
from src.db import models as db

# --- DATA MODEL --- #


class Wakeup(Schema):
    """This object represents a wakeup: a turn the agent scheduled in Proactive mode."""

    id: UUID = Field(description="Unique wakeup identifier")
    chat_id: UUID = Field(description="Unique chat identifier")
    due_at: datetime = Field(description="Date and time the turn starts")
    reason: str = Field(description="Why the agent wakes up")
    status: db.WakeupStatus = Field(description="Whether it is pending, fired or cancelled")
    created_at: datetime = Field(description="Date and time of wakeup creation")
    fired_at: datetime | None = Field(description="_Optional_. Date and time the turn started")


# --- QUERY PARAMS --- #


class WakeupsFilter(Input):
    """This object represents the filter of wakeups."""

    status: list[db.WakeupStatus] = Field(
        default=[db.WakeupStatus.PENDING],
        alias="filter[status]",
        description="_Optional_. Filter by one or more `status` values; *pending* by default",
    )


class WakeupsSort(StrEnum):
    """Fields wakeups can be sorted by."""

    ID = "id"
    CREATED_AT = "created_at"
    DUE_AT = "due_at"


class ListWakeupsInput(WakeupsFilter, PageQuery):
    """This object represents the input used to list wakeups."""

    sort: WakeupsSort = Field(default=WakeupsSort.ID, description="_Optional_. Sort field")
    direction: SortDirection = Field(
        default=SortDirection.ASC,
        description="_Optional_. Sort direction. Can be either `asc` or `desc`",
    )


# --- RESULTS --- #


class ListWakeupsResult(ListResult[Wakeup]):
    """This object represents the result when wakeups are listed."""


class DeleteWakeupResult(Result[Wakeup]):
    """This object represents the result when the wakeup is cancelled."""
