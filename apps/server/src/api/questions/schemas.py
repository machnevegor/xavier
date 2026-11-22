"""Questions of the agent and the user's answers."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import Field, JsonValue

from src.api.common import Input, ListResult, PageQuery, Result, Schema, SortDirection
from src.db import models as db

# --- DATA MODEL --- #


class Question(Schema):
    """This object represents a question of the agent and its answer."""

    id: UUID = Field(description="Unique question identifier")
    chat_id: UUID = Field(description="Chat the question belongs to")
    turn_id: UUID = Field(description="Turn that asked the question")
    call_id: str = Field(description="Identifier of the `ask_user_question` call")
    questions: JsonValue = Field(
        description="Items with `id`, `question`, `options` (`label`, `description`) and "
        "`multiSelect`; no options means free text"
    )
    timeout_s: int = Field(description="How long the agent waits; *-1* waits for the answer")
    status: db.QuestionStatus = Field(description="Question status")
    answers: JsonValue | None = Field(description="_Optional_. The user's answers")
    waiting: bool = Field(description="_True_, if the chat waits for this answer to continue")
    created_at: datetime = Field(description="Date and time of question creation")
    answered_at: datetime | None = Field(description="_Optional_. Date and time of the answer")


# --- QUERY PARAMS --- #


class QuestionsFilter(Input):
    """This object represents the filter of the question list."""

    status: list[db.QuestionStatus] | None = Field(
        default=None,
        alias="filter[status]",
        description="_Optional_. Filter by one or more `status` values",
    )


class QuestionsSort(StrEnum):
    """Sort field of the question list."""

    ID = "id"
    CREATED_AT = "created_at"


class ListQuestionsInput(QuestionsFilter, PageQuery):
    """This object represents the input used to list questions."""

    sort: QuestionsSort = Field(
        default=QuestionsSort.CREATED_AT, description="_Optional_. Sort field"
    )
    direction: SortDirection = Field(
        default=SortDirection.ASC,
        description="_Optional_. Sort direction. Can be either `asc` or `desc`",
    )


# --- INPUTS --- #


class AnswerQuestionInput(Input):
    """This object represents the answer to one question of a call.

    Neither field skips the question.
    """

    id: str = Field(description="Identifier of the question in the call")
    selected: list[str] = Field(
        default=[], description="Option labels; one at most unless the question allows several"
    )
    custom: str | None = Field(
        default=None,
        max_length=10_000,
        description="_Optional_. Free text; for a single-choice question it replaces the selection",
    )


# --- ARGS --- #


class AnswerQuestionArgs(Input):
    """This object represents the arguments used to answer every question of one call."""

    data: list[AnswerQuestionInput] = Field(min_length=1)


# --- RESULTS --- #


class ListQuestionsResult(ListResult[Question]):
    """This object represents the result when questions are listed."""


class ReadQuestionResult(Result[Question]):
    """This object represents the result when the question is read."""


class AnswerQuestionResult(Result[Question]):
    """This object represents the result when the question is answered."""
