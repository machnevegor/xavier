"""Code runs."""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Self
from uuid import UUID

from pydantic import AfterValidator, Field, JsonValue, model_validator

from src.api.common import Input, ListResult, PageQuery, Result, Schema, SortDirection
from src.db import models as db
from src.policy.urls import workspace_path

# --- DATA MODEL --- #


class Language(StrEnum):
    """Runtime of a code run."""

    PYTHON = "python"
    NODE = "node"
    BASH = "bash"


class Execution(Schema):
    """This object represents a code run and its result."""

    id: UUID = Field(description="Unique execution identifier")
    chat_id: UUID | None = Field(description="Unique chat identifier; *null* for API runs")
    status: db.ExecutionStatus = Field(description="Whether it is pending, running or finished")
    language: Language = Field(description="Runtime of the code")
    code: str | None = Field(description="Program to run")
    command: str | None = Field(description="Bash command line")
    network: bool = Field(description="_True_, if the container reaches the internet")
    export: list[str] = Field(description="Paths under `/workspace` that become artifacts")
    input_artifact_ids: list[UUID] = Field(description="Artifacts copied into `/workspace`")
    exit_code: int | None = Field(description="Exit code of a finished run")
    stdout: str = Field(description="Standard output")
    stderr: str = Field(description="Standard error")
    truncated: bool = Field(description="_True_, if output was cut at the configured limit")
    output_artifact_ids: list[UUID] = Field(description="Artifacts produced by `export`")
    metrics: JsonValue = Field(description="Runtime metrics of the container")
    error: str | None = Field(description="Why the run did not finish cleanly")
    created_at: datetime = Field(description="Date and time of execution creation")
    started_at: datetime | None = Field(description="Date and time the run started")
    finished_at: datetime | None = Field(description="Date and time the run finished")


# --- QUERY PARAMS --- #


class ExecutionsFilter(Input):
    """Which executions to list."""

    chat_id: list[UUID] | None = Field(
        default=None,
        alias="filter[chat_id]",
        description="_Optional_. Filter by one or more `chat_id` values",
    )
    status: list[db.ExecutionStatus] | None = Field(
        default=None,
        alias="filter[status]",
        description="_Optional_. Filter by one or more `status` values",
    )


class ExecutionsSort(StrEnum):
    """Fields executions can be sorted by."""

    ID = "id"
    CREATED_AT = "created_at"


class ListExecutionsInput(ExecutionsFilter, PageQuery):
    """This object represents the input used to list executions."""

    sort: ExecutionsSort = Field(
        default=ExecutionsSort.CREATED_AT, description="_Optional_. Sort field"
    )
    direction: SortDirection = Field(
        default=SortDirection.ASC,
        description="_Optional_. Sort direction. Can be either `asc` or `desc`",
    )


# --- INPUTS --- #


class CreateExecutionInput(Input):
    """This object represents the input used to create the execution.

    Exactly one of `code` and `command` is required.
    """

    language: Language = Field(
        default=Language.PYTHON, description="_Optional_. Runtime of the code"
    )
    code: str | None = Field(
        default=None, max_length=200_000, description="_Optional_. Program to run"
    )
    command: str | None = Field(
        default=None, max_length=10_000, description="_Optional_. A bash command line"
    )
    input_artifact_ids: list[UUID] = Field(
        default=[],
        description="_Optional_. Artifacts copied into `/workspace` under their names before the "
        "run",
    )
    network: bool = Field(
        default=False,
        description="_Optional_. Internet access; internal addresses stay unreachable",
    )
    export: list[Annotated[str, AfterValidator(workspace_path)]] = Field(
        default=[],
        max_length=20,
        description="_Optional_. Paths under `/workspace` that become artifacts after the run",
    )

    @model_validator(mode="after")
    def _one_program(self) -> Self:
        if (self.code is None) == (self.command is None):
            raise ValueError("pass exactly one of `code` and `command`")
        return self


# --- ARGS --- #


class CreateExecutionArgs(Input):
    """This object represents the arguments used to create the execution."""

    data: CreateExecutionInput


# --- RESULTS --- #


class CreateExecutionResult(Result[Execution]):
    """This object represents the result when the execution is created."""


class ListExecutionsResult(ListResult[Execution]):
    """This object represents the result when executions are listed."""


class ReadExecutionResult(Result[Execution]):
    """This object represents the result when the execution is read."""


class CancelExecutionResult(Result[Execution]):
    """This object represents the result when cancellation of the execution is requested."""
