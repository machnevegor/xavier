"""Chat schedules: messages posted to the agent on a timetable."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Self
from uuid import UUID

from pydantic import Field, model_validator

from src.api.common import Input, ListResult, OffsetQuery, Result, Schema, SortDirection
from src.db import models as db
from src.orchestration.models import ChatTiming
from src.policy.model import Mode

# --- DATA MODEL --- #


class ScheduleTemplate(Schema):
    """This object represents the settings of the chats a schedule creates."""

    title: str = Field(description="Title of created chats")
    model: str = Field(description="Model identifier")
    reasoning: str = Field(description="Reasoning level of the model")
    mode: Mode = Field(description="How much the agent may do unasked")
    tool_groups: list[db.ToolGroup] = Field(description="Enabled tool groups")
    execute_network: bool = Field(description="_True_, if `code_run` containers reach the internet")
    budget_usd: float = Field(description="Spending limit in USD")
    max_steps: int | None = Field(description="Step limit of one turn. _null_ sets no step limit")


class Schedule(Schema):
    """This object represents a chat schedule."""

    id: UUID = Field(description="Unique schedule identifier")
    name: str = Field(description="Name of the schedule")
    timing: ChatTiming = Field(description="When the message is posted")
    text: str = Field(description="Message posted into the chat")
    chat_id: UUID | None = Field(
        description="Pinned chat; *null* creates a chat on every run from `template`"
    )
    template: ScheduleTemplate | None = Field(
        description="Settings of the chats it creates; *null* with a pinned chat"
    )
    paused: bool = Field(description="_True_, if posting is stopped")
    next_runs: list[datetime] = Field(description="Next scheduled run times")
    recent_runs: list[datetime] = Field(description="Recent scheduled run times")
    created_at: datetime = Field(description="Date and time of schedule creation")


# --- QUERY PARAMS --- #


class SchedulesFilter(Input):
    """Which schedules to list."""

    chat_id: list[UUID] | None = Field(
        default=None,
        alias="filter[chat_id]",
        description="_Optional_. Filter by one or more `chat_id` values",
    )
    paused: bool | None = Field(
        default=None, alias="filter[paused]", description="_Optional_. Filter by `paused`"
    )


class SchedulesSort(StrEnum):
    """Fields schedules can be sorted by."""

    ID = "id"
    NAME = "name"
    CREATED_AT = "created_at"


class ListSchedulesInput(SchedulesFilter, OffsetQuery):
    """This object represents the input used to list schedules."""

    sort: SchedulesSort = Field(
        default=SchedulesSort.CREATED_AT, description="_Optional_. Sort field"
    )
    direction: SortDirection = Field(
        default=SortDirection.ASC,
        description="_Optional_. Sort direction. Can be either `asc` or `desc`",
    )


# --- INPUTS --- #


class CreateScheduleInput(Input):
    """This object represents the input used to create the schedule.

    The message goes into `chat_id`, or into a new chat per run with the settings below,
    resolved with the defaults when the schedule is stored.
    """

    name: str = Field(min_length=1, max_length=100, description="Name of the schedule")
    timing: ChatTiming = Field(description="When to post; a one-off `at` must be in the future")
    text: str = Field(min_length=1, max_length=20_000, description="Message posted into the chat")
    chat_id: UUID | None = Field(
        default=None,
        description="_Optional_. Pinned chat; omitted creates a chat on every run",
    )
    title: str | None = Field(
        default=None, max_length=200, description="_Optional_. Title of created chats"
    )
    model: str | None = Field(
        default=None, description="_Optional_. Model identifier from `GET /models`"
    )
    reasoning: str | None = Field(
        default=None, description="_Optional_. One of the model's reasoning levels"
    )
    mode: Mode | None = Field(
        default=None, description="_Optional_. How much the agent may do unasked"
    )
    tool_groups: list[db.ToolGroup] = Field(
        default=[db.ToolGroup.OBSERVE],
        description="_Optional_. Enabled tool groups of created chats",
    )
    execute_network: bool = Field(
        default=False, description="_Optional_. _True_, if `code_run` containers reach the internet"
    )
    budget_usd: float | None = Field(
        default=None, ge=0, description="_Optional_. Spending limit in USD"
    )
    max_steps: Annotated[int, Field(ge=1)] | None = Field(
        default=None,
        description="_Optional_. Step limit of created chats. Omitted uses the configured default. "
        "_null_ sets no step limit",
    )
    paused: bool = Field(default=False, description="_Optional_. _True_, to start stopped")

    @model_validator(mode="after")
    def _future(self) -> Self:
        if self.timing.at is not None and self.timing.at <= datetime.now(UTC):
            raise ValueError("`timing.at` must be in the future")
        return self


# --- ARGS --- #


class CreateScheduleArgs(Input):
    """This object represents the arguments used to create the schedule."""

    data: CreateScheduleInput


class ReplaceScheduleArgs(Input):
    """This object represents the arguments used to replace the schedule."""

    data: CreateScheduleInput


# --- RESULTS --- #


class CreateScheduleResult(Result[Schedule]):
    """This object represents the result when the schedule is created."""


class ListSchedulesResult(ListResult[Schedule]):
    """This object represents the result when schedules are listed."""


class ReadScheduleResult(Result[Schedule]):
    """This object represents the result when the schedule is read."""


class ReplaceScheduleResult(Result[Schedule]):
    """This object represents the result when the schedule is replaced."""


class DeleteScheduleResult(Result[Schedule]):
    """This object represents the result when the schedule is deleted."""


class PauseScheduleResult(Result[Schedule]):
    """This object represents the result when the schedule is paused."""


class ResumeScheduleResult(Result[Schedule]):
    """This object represents the result when the schedule is resumed."""


class TriggerScheduleResult(Result[Schedule]):
    """This object represents the result when the schedule is triggered."""
