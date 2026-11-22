"""Actions: tool calls with their moderation and outcome."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import Field, JsonValue

from src.api.common import Input, ListResult, PageQuery, Result, Schema, SortDirection
from src.db import models as db
from src.policy.model import Effect, Risk

# --- DATA MODEL --- #


class Action(Schema):
    """This object represents an action: a tool call with its moderation and outcome."""

    id: UUID = Field(description="Unique action identifier")
    chat_id: UUID = Field(description="Unique chat identifier")
    turn_id: UUID = Field(description="Unique turn identifier")
    tool: str = Field(description="Name of the tool")
    arguments: JsonValue = Field(description="Arguments of the call, secrets redacted")
    resources: JsonValue = Field(description="Hosts, URLs, paths and commands the call touches")
    risk: Risk = Field(description="Risk of the call")
    effect: Effect = Field(description="What the guardrails decided")
    rule_id: UUID | None = Field(description="_Optional_. Guardrail rule that decided")
    status: db.ActionStatus = Field(description="Where the call is")
    error_code: str | None = Field(description="_Optional_. Stable code of the failure")
    error: str | None = Field(description="_Optional_. What went wrong")
    decision: db.Decision | None = Field(description="_Optional_. Operator decision")
    created_at: datetime = Field(description="Date and time of action creation")
    decided_at: datetime | None = Field(description="_Optional_. Date and time of the decision")
    finished_at: datetime | None = Field(description="_Optional_. Date and time the call finished")


# --- QUERY PARAMS --- #


class ActionsFilter(Input):
    """This object represents the filter of actions."""

    turn_id: list[UUID] | None = Field(
        default=None,
        alias="filter[turn_id]",
        description="_Optional_. Filter by one or more `turn_id` values",
    )
    status: list[db.ActionStatus] | None = Field(
        default=None,
        alias="filter[status]",
        description="_Optional_. Filter by one or more `status` values",
    )


class ActionsSort(StrEnum):
    """Fields actions can be sorted by."""

    ID = "id"
    CREATED_AT = "created_at"


class ListActionsInput(ActionsFilter, PageQuery):
    """This object represents the input used to list actions."""

    sort: ActionsSort = Field(default=ActionsSort.ID, description="_Optional_. Sort field")
    direction: SortDirection = Field(
        default=SortDirection.ASC,
        description="_Optional_. Sort direction. Can be either `asc` or `desc`",
    )


# --- RESULTS --- #


class ListActionsResult(ListResult[Action]):
    """This object represents the result when actions are listed."""


class ReadActionResult(Result[Action]):
    """This object represents the result when the action is read."""
