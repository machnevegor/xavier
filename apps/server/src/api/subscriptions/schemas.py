"""Alert subscriptions."""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Self
from uuid import UUID

from pydantic import AfterValidator, Field, model_validator

from src.api.common import Input, ListResult, PageQuery, Result, Schema, SortDirection
from src.db import models as db
from src.policy.urls import normalize_url

# --- DATA MODEL --- #


class Subscription(Schema):
    """This object represents an alert subscription; the webhook secret is never returned."""

    id: UUID = Field(description="Unique subscription identifier")
    channel: db.SubscriptionChannel = Field(description="*ui* or *webhook*")
    source_id: UUID | None = Field(
        description="Unique source identifier; *null* covers every source of the user"
    )
    kinds: list[db.AlertKind] = Field(
        description="Alert kinds delivered; empty delivers every kind"
    )
    url: str | None = Field(description="Webhook URL")
    created_at: datetime = Field(description="Date and time of subscription creation")
    updated_at: datetime = Field(description="Date and time of last subscription update")
    updated_by: UUID | None = Field(description="User who last changed the subscription")


# --- QUERY PARAMS --- #


class SubscriptionsFilter(Input):
    """Which subscriptions to list."""

    channel: list[db.SubscriptionChannel] | None = Field(
        default=None,
        alias="filter[channel]",
        description="_Optional_. Filter by one or more `channel` values",
    )
    source_id: list[UUID] | None = Field(
        default=None,
        alias="filter[source_id]",
        description="_Optional_. Filter by one or more `source_id` values",
    )


class SubscriptionsSort(StrEnum):
    """Fields subscriptions can be sorted by."""

    ID = "id"
    CREATED_AT = "created_at"
    UPDATED_AT = "updated_at"


class ListSubscriptionsInput(SubscriptionsFilter, PageQuery):
    """This object represents the input used to list subscriptions."""

    sort: SubscriptionsSort = Field(
        default=SubscriptionsSort.CREATED_AT, description="_Optional_. Sort field"
    )
    direction: SortDirection = Field(
        default=SortDirection.ASC,
        description="_Optional_. Sort direction. Can be either `asc` or `desc`",
    )


# --- INPUTS --- #


class CreateSubscriptionInput(Input):
    """This object represents the input used to create the subscription.

    A webhook needs `url` and `secret`, and only a webhook has them.
    """

    channel: db.SubscriptionChannel = Field(description="*ui* or *webhook*")
    source_id: UUID | None = Field(
        default=None,
        description="_Optional_. Source to follow; omitted covers every source of the user",
    )
    kinds: list[db.AlertKind] = Field(
        default=[], description="_Optional_. Alert kinds delivered; empty delivers every kind"
    )
    url: Annotated[str, AfterValidator(lambda url: normalize_url(url)[0])] | None = Field(
        default=None,
        description="_Optional_. Webhook URL; it receives a POST with the alert as JSON",
    )
    secret: str | None = Field(
        default=None,
        min_length=16,
        max_length=200,
        description="_Optional_. Webhook signing secret, write-only. "
        "`X-Xavier-Signature: t=<unix time>,v1=<hex>` carries the HMAC-SHA256 of "
        "`<unix time>.<body>`",
    )

    @model_validator(mode="after")
    def _webhook_fields(self) -> Self:
        webhook = self.channel is db.SubscriptionChannel.WEBHOOK
        if webhook != (self.url is not None) or webhook != (self.secret is not None):
            raise ValueError("`url` and `secret` are required for webhooks and only for them")
        return self


# --- ARGS --- #


class CreateSubscriptionArgs(Input):
    """This object represents the arguments used to create the subscription."""

    data: CreateSubscriptionInput


class ReplaceSubscriptionArgs(Input):
    """This object represents the arguments used to replace the subscription."""

    data: CreateSubscriptionInput


# --- RESULTS --- #


class CreateSubscriptionResult(Result[Subscription]):
    """This object represents the result when the subscription is created."""


class ListSubscriptionsResult(ListResult[Subscription]):
    """This object represents the result when subscriptions are listed."""


class ReadSubscriptionResult(Result[Subscription]):
    """This object represents the result when the subscription is read."""


class ReplaceSubscriptionResult(Result[Subscription]):
    """This object represents the result when the subscription is replaced."""


class DeleteSubscriptionResult(Result[Subscription]):
    """This object represents the result when the subscription is deleted."""
