"""Observe alerts."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import Field, JsonValue

from src.api.common import Input, ListResult, PageQuery, Result, Schema, SortDirection
from src.db import models as db

# --- DATA MODEL --- #


class Alert(Schema):
    """This object represents an Observe alert."""

    id: UUID = Field(description="Unique alert identifier")
    source_id: UUID = Field(description="Unique source identifier")
    kind: db.AlertKind = Field(description="What the alert reports")
    status: db.AlertStatus = Field(description="Whether it is open, acknowledged or resolved")
    resource_key: str = Field(description="Resource the alert belongs to")
    message: str = Field(description="What happened, for people")
    data: JsonValue = Field(description="Structured details of the alert")
    version_id: UUID | None = Field(description="Unique identifier of the version that raised it")
    occurrences: int = Field(description="Repeats while unresolved")
    created_at: datetime = Field(description="Date and time of alert creation")
    updated_at: datetime = Field(description="Date and time of last alert update")
    updated_by: UUID | None = Field(description="User who last changed the alert")
    acknowledged_at: datetime | None = Field(description="Date and time it was marked as seen")
    resolved_at: datetime | None = Field(description="Date and time it was closed")


# --- QUERY PARAMS --- #


class AlertsFilter(Input):
    """Which alerts to list."""

    status: list[db.AlertStatus] | None = Field(
        default=None,
        alias="filter[status]",
        description="_Optional_. Filter by one or more `status` values",
    )
    source_id: list[UUID] | None = Field(
        default=None,
        alias="filter[source_id]",
        description="_Optional_. Filter by one or more `source_id` values",
    )
    kind: list[db.AlertKind] | None = Field(
        default=None,
        alias="filter[kind]",
        description="_Optional_. Filter by one or more `kind` values",
    )


class AlertsSort(StrEnum):
    """Fields alerts can be sorted by."""

    ID = "id"
    CREATED_AT = "created_at"
    UPDATED_AT = "updated_at"


class ListAlertsInput(AlertsFilter, PageQuery):
    """This object represents the input used to list alerts."""

    sort: AlertsSort = Field(default=AlertsSort.ID, description="_Optional_. Sort field")
    direction: SortDirection = Field(
        default=SortDirection.DESC,
        description="_Optional_. Sort direction. Can be either `asc` or `desc`",
    )


# --- RESULTS --- #


class ListAlertsResult(ListResult[Alert]):
    """This object represents the result when alerts are listed."""


class AcknowledgeAlertResult(Result[Alert]):
    """This object represents the result when the alert is acknowledged."""


class ResolveAlertResult(Result[Alert]):
    """This object represents the result when the alert is resolved."""
