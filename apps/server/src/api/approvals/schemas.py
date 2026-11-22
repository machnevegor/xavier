"""Decisions of the user on actions waiting for approval."""

from enum import StrEnum
from uuid import UUID

from pydantic import Field

from src.api.actions.schemas import Action
from src.api.common import Input, ListResult, PageQuery, Result, SortDirection
from src.db import models as db
from src.policy.model import Breadth, Scope

MAX_DECISIONS = 50

# --- QUERY PARAMS --- #


class ApprovalsFilter(Input):
    """This object represents the filter of the approval list."""

    chat_id: list[UUID] | None = Field(
        default=None,
        alias="filter[chat_id]",
        description="_Optional_. Filter by one or more `chat_id` values",
    )
    tool: list[str] | None = Field(
        default=None,
        alias="filter[tool]",
        description="_Optional_. Filter by one or more `tool` values",
    )


class ApprovalsSort(StrEnum):
    """Sort field of the approval list."""

    ID = "id"
    CREATED_AT = "created_at"


class ListApprovalsInput(ApprovalsFilter, PageQuery):
    """This object represents the input used to list actions waiting for a decision."""

    sort: ApprovalsSort = Field(
        default=ApprovalsSort.CREATED_AT, description="_Optional_. Sort field"
    )
    direction: SortDirection = Field(
        default=SortDirection.ASC,
        description="_Optional_. Sort direction. Can be either `asc` or `desc`",
    )


# --- INPUTS --- #


class DecideActionInput(Input):
    """This object represents the input used to decide the action."""

    decision: db.Decision = Field(description="Approve, reject or cancel")
    scope: Scope = Field(
        default=Scope.ONCE,
        description="_Optional_. *chat* and *always* approvals also allow later calls of the "
        "tool on these resources: with a rule of this chat, or with a global rule shown in "
        "Guardrails",
    )
    breadth: Breadth = Field(
        default=Breadth.EXACT,
        description="_Optional_. Resources a *chat* or *always* rule covers: the *exact* URLs, "
        "paths and commands, the hosts with their *subdomains*, *public_web* for any public "
        "website, or every path under the same folder (*prefix*)",
    )


class DecideChatActionInput(DecideActionInput):
    """This object represents the input used to decide one action of the chat."""

    action_id: UUID = Field(description="Unique action identifier")


# --- ARGS --- #


class DecideActionArgs(Input):
    """This object represents the arguments used to decide the action."""

    data: DecideActionInput


class DecideActionsArgs(Input):
    """This object represents the arguments used to decide several actions of the chat."""

    data: list[DecideChatActionInput] = Field(min_length=1, max_length=MAX_DECISIONS)


# --- RESULTS --- #


class ListApprovalsResult(ListResult[Action]):
    """This object represents the result when actions waiting for a decision are listed."""


class DecideActionResult(Result[Action]):
    """This object represents the result when the action is decided."""


class DecideActionsResult(Result[list[Action]]):
    """This object represents the result when the actions are decided, in request order."""
