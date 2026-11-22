"""Operator messages of a chat."""

from datetime import datetime
from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import Field, model_validator

from src.api.common import Input, ListResult, PageQuery, Result, Schema, SortDirection
from src.db import models as db

# --- DATA MODEL --- #


class Message(Schema):
    """This object represents an operator message of a chat."""

    id: UUID = Field(description="Unique message identifier")
    chat_id: UUID = Field(description="Unique chat identifier")
    text: str = Field(description="Text of the message, Markdown")
    image_artifact_ids: list[UUID] = Field(description="Attached images")
    file_artifact_ids: list[UUID] = Field(description="Attached files")
    status: db.MessageStatus = Field(
        description="*queued* until a turn takes it; *superseded* once revised or rewound"
    )
    turn_id: UUID | None = Field(
        description="_Optional_. Unique identifier of the turn that took it"
    )
    kind: db.MessageKind = Field(description="*answer* carries a late answer to a question")
    question_id: UUID | None = Field(description="_Optional_. Question a late answer answers")
    revision_of: UUID | None = Field(description="_Optional_. Original message this one revises")
    superseded_at: datetime | None = Field(
        description="_Optional_. Date and time of message supersession"
    )
    created_at: datetime = Field(description="Date and time of message creation")


# --- QUERY PARAMS --- #


class MessagesFilter(Input):
    """This object represents the filter of messages."""

    include_superseded: bool = Field(
        default=False,
        alias="filter[include_superseded]",
        description="_Optional_. Also list messages a revision replaced",
    )


class MessagesSort(StrEnum):
    """Fields messages can be sorted by."""

    ID = "id"
    CREATED_AT = "created_at"


class ListMessagesInput(MessagesFilter, PageQuery):
    """This object represents the input used to list messages."""

    sort: MessagesSort = Field(default=MessagesSort.ID, description="_Optional_. Sort field")
    direction: SortDirection = Field(
        default=SortDirection.ASC,
        description="_Optional_. Sort direction. Can be either `asc` or `desc`",
    )


# --- INPUTS --- #


class CreateMessageInput(Input):
    """This object represents the input used to create the message.

    At least one of text, images and files is required.
    """

    text: str = Field(default="", max_length=200_000, description="Text of the message, Markdown")
    image_artifact_ids: list[UUID] = Field(default=[], description="Uploaded images to attach")
    file_artifact_ids: list[UUID] = Field(
        default=[],
        description="Uploaded files, delivered into `/workspace/inbox/` when the chat has "
        "`execute` or `compute`",
    )

    @model_validator(mode="after")
    def _not_empty(self) -> Self:
        if not self.text.strip() and not self.image_artifact_ids and not self.file_artifact_ids:
            raise ValueError("a message needs text, images or files")
        return self


# --- ARGS --- #


class CreateMessageArgs(Input):
    """This object represents the arguments used to create the message."""

    data: CreateMessageInput


class ReviseMessageArgs(Input):
    """This object represents the arguments used to revise the message."""

    data: CreateMessageInput


# --- RESULTS --- #


class CreateMessageResult(Result[Message]):
    """This object represents the result when the message is created."""


class ListMessagesResult(ListResult[Message]):
    """This object represents the result when messages are listed."""


class ReviseMessageResult(Result[Message]):
    """This object represents the result when the message is revised."""


class RegenerateMessageResult(Result[Message]):
    """This object represents the result when the message is sent again."""
