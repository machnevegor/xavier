"""Chats: their settings, status and spending."""

from datetime import datetime
from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import Field

from src.api.common import Input, ListResult, PageQuery, Result, Schema, SortDirection
from src.api.messages.schemas import CreateMessageInput
from src.db import models as db
from src.policy.model import Mode

# --- DATA MODEL --- #


class Chat(Schema):
    """This object represents a chat."""

    id: UUID = Field(description="Unique chat identifier")
    title: str = Field(description="Title of the chat")
    title_source: db.TitleSource = Field(
        description="*user* pins the title: generated titles no longer replace it"
    )
    status: db.ChatStatus = Field(description="What the chat is doing")
    pause_reason: db.PauseReason | None = Field(description="_Optional_. Why the chat is paused")
    model: str = Field(description="Model identifier from `GET /models`")
    reasoning: str = Field(description="Reasoning level of the model")
    mode: Mode = Field(description="How much the agent may do without asking")
    tool_groups: list[str] = Field(description="Enabled tool groups")
    execute_network: bool = Field(description="_True_, if `code_run` containers reach the internet")
    proactive: bool = Field(
        description="_True_, if the agent may schedule wakeups and react to finished jobs and "
        "alerts on its own"
    )
    declined_capabilities: list[str] = Field(
        description="Capabilities the operator declined; the agent does not ask for them again "
        "until the operator changes that setting"
    )
    budget_usd: float = Field(description="Spending limit in USD")
    max_steps: int | None = Field(description="Step limit of one turn. _null_ sets no step limit")
    spent_usd: float = Field(description="Spending so far in USD")
    computer_active: bool = Field(default=False, description="_True_, if a Computer is running")
    created_at: datetime = Field(description="Date and time of chat creation")
    created_by: UUID = Field(description="User who owns the chat")
    updated_at: datetime = Field(description="Date and time of last chat update")
    updated_by: UUID | None = Field(description="User who last changed the chat")
    deleted_at: datetime | None = Field(description="_Optional_. Date and time of chat deletion")


# --- QUERY PARAMS --- #


class ChatsFilter(Input):
    """This object represents the filter of chats."""

    status: list[db.ChatStatus] | None = Field(
        default=None,
        alias="filter[status]",
        description="_Optional_. Filter by one or more `status` values",
    )


class ChatsSort(StrEnum):
    """Fields chats can be sorted by."""

    ID = "id"
    CREATED_AT = "created_at"
    UPDATED_AT = "updated_at"


class ListChatsInput(ChatsFilter, PageQuery):
    """This object represents the input used to list chats."""

    sort: ChatsSort = Field(default=ChatsSort.UPDATED_AT, description="_Optional_. Sort field")
    direction: SortDirection = Field(
        default=SortDirection.DESC,
        description="_Optional_. Sort direction. Can be either `asc` or `desc`",
    )


# --- INPUTS --- #


class CreateChatInput(CreateMessageInput):
    """This object represents the input used to create the chat with its first message.

    Omitted settings take the configured defaults.
    """

    title: str | None = Field(
        default=None, max_length=200, description="_Optional_. Title of the chat; pins it"
    )
    model: str | None = Field(
        default=None, description="_Optional_. Model identifier from `GET /models`"
    )
    reasoning: str | None = Field(
        default=None, description="_Optional_. One of the reasoning levels of the model"
    )
    mode: Mode | None = Field(
        default=None, description="_Optional_. How much the agent may do without asking"
    )
    tool_groups: list[db.ToolGroup] = Field(
        default=[db.ToolGroup.OBSERVE], description="_Optional_. Enabled tool groups"
    )
    execute_network: bool = Field(
        default=False, description="_Optional_. _True_, if `code_run` containers reach the internet"
    )
    proactive: bool = Field(
        default=False,
        description="_Optional_. _True_, if the agent may schedule wakeups and react to events "
        "on its own",
    )
    budget_usd: float | None = Field(
        default=None, ge=0, description="_Optional_. Spending limit in USD"
    )
    max_steps: Annotated[int, Field(ge=1)] | None = Field(
        default=None,
        description="_Optional_. Step limit of one turn. Omitted uses the configured default. "
        "_null_ sets no step limit",
    )


class UpdateChatInput(Input):
    """This object represents the input used to update the chat.

    Omitted fields keep their values. Changes apply from the next turn.
    """

    title: str | None = Field(
        default=None, min_length=1, max_length=200, description="_Optional_. Title; pins it"
    )
    model: str | None = Field(
        default=None,
        description="_Optional_. Model identifier from `GET /models`; the reasoning level falls "
        "back to the model's first one when the current level is not offered",
    )
    reasoning: str | None = Field(
        default=None, description="_Optional_. One of the reasoning levels of the model"
    )
    mode: Mode | None = Field(
        default=None, description="_Optional_. How much the agent may do without asking"
    )
    tool_groups: list[db.ToolGroup] | None = Field(
        default=None, description="_Optional_. Enabled tool groups"
    )
    execute_network: bool | None = Field(
        default=None, description="_Optional_. _True_, if `code_run` containers reach the internet"
    )
    proactive: bool | None = Field(
        default=None, description="_Optional_. Proactive mode; turning it off cancels wakeups"
    )
    budget_usd: float | None = Field(
        default=None, ge=0, description="_Optional_. Spending limit in USD"
    )
    max_steps: Annotated[int, Field(ge=1)] | None = Field(
        default=None,
        description="_Optional_. Step limit of one turn. Omitted keeps the current value. "
        "_null_ sets no step limit",
    )


# --- ARGS --- #


class CreateChatArgs(Input):
    """This object represents the arguments used to create the chat."""

    data: CreateChatInput


class UpdateChatArgs(Input):
    """This object represents the arguments used to update the chat."""

    data: UpdateChatInput


# --- RESULTS --- #


class CreateChatResult(Result[Chat]):
    """This object represents the result when the chat is created."""


class ListChatsResult(ListResult[Chat]):
    """This object represents the result when chats are listed."""


class ReadChatResult(Result[Chat]):
    """This object represents the result when the chat is read."""


class UpdateChatResult(Result[Chat]):
    """This object represents the result when the chat is updated."""


class DeleteChatResult(Result[Chat]):
    """This object represents the result when the chat is deleted."""


class PauseChatResult(Result[Chat]):
    """This object represents the result when the chat is paused."""


class ResumeChatResult(Result[Chat]):
    """This object represents the result when the chat is resumed."""


class StopChatResult(Result[Chat]):
    """This object represents the result when the chat is stopped."""
