"""Alert subscriptions deliver Observe alerts to the UI or to a webhook.

A subscription follows one source of the user, or every source when `source_id` is *null*, and
delivers the listed alert `kinds`, or every kind when the list is empty. A webhook receives each
new alert as a JSON POST signed with its secret and is retried on failure; the secret is never
returned. Deleting a source deletes its subscriptions.
"""

import uuid
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.common import paginate, router
from src.api.dependencies import CurrentUserDep, ServicesDep, SessionDep
from src.api.subscriptions.schemas import (
    CreateSubscriptionArgs,
    CreateSubscriptionResult,
    DeleteSubscriptionResult,
    ListSubscriptionsInput,
    ListSubscriptionsResult,
    ReadSubscriptionResult,
    ReplaceSubscriptionArgs,
    ReplaceSubscriptionResult,
    Subscription,
    SubscriptionsSort,
)
from src.db import models as db
from src.errors import NotFoundError

router = router("/subscriptions", "subscriptions")

SORT = {
    SubscriptionsSort.ID: db.Subscription.id,
    SubscriptionsSort.CREATED_AT: db.Subscription.created_at,
    SubscriptionsSort.UPDATED_AT: db.Subscription.updated_at,
}


async def get_owned_subscription(
    subscription_id: UUID, principal: CurrentUserDep, session: SessionDep
) -> db.Subscription:
    """Return the user's subscription."""
    subscription = await session.get(db.Subscription, subscription_id)
    if subscription is None or subscription.created_by != principal.user_id:
        raise NotFoundError(f"subscription {subscription_id} not found")
    return subscription


OwnedSubscriptionDep = Annotated[db.Subscription, Depends(get_owned_subscription)]


@router.get("")
async def list_subscriptions(
    principal: CurrentUserDep,
    session: SessionDep,
    params: Annotated[ListSubscriptionsInput, Query()],
) -> ListSubscriptionsResult:
    """List the user's alert subscriptions."""
    query = select(db.Subscription).where(db.Subscription.created_by == principal.user_id)
    if params.channel:
        query = query.where(db.Subscription.channel.in_(params.channel))
    if params.source_id:
        query = query.where(db.Subscription.source_id.in_(params.source_id))
    rows, info = await paginate(
        session,
        query,
        params,
        sort=SORT[params.sort],
        direction=params.direction,
        key=db.Subscription.id,
    )
    return ListSubscriptionsResult(
        data=[Subscription.model_validate(row) for row in rows], info=info
    )


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_subscription(
    body: CreateSubscriptionArgs,
    principal: CurrentUserDep,
    session: SessionDep,
    services: ServicesDep,
) -> CreateSubscriptionResult:
    """Subscribe the UI or a webhook to alerts."""
    data = body.data
    if data.source_id is not None and not await _source_owned(
        session, data.source_id, principal.user_id
    ):
        raise NotFoundError(f"source {data.source_id} not found")
    subscription = db.Subscription(
        id=uuid.uuid7(),
        created_by=principal.user_id,
        channel=data.channel,
        source_id=data.source_id,
        kinds=sorted({kind.value for kind in data.kinds}),
        url=data.url,
        secret=services.vault.encrypt(data.secret) if data.secret is not None else None,
        updated_by=principal.user_id,
    )
    session.add(subscription)
    await session.flush()
    await session.refresh(subscription)
    return CreateSubscriptionResult(data=Subscription.model_validate(subscription))


@router.get("/{subscription_id}")
async def read_subscription(subscription: OwnedSubscriptionDep) -> ReadSubscriptionResult:
    """Return a subscription."""
    return ReadSubscriptionResult(data=Subscription.model_validate(subscription))


@router.put("/{subscription_id}")
async def replace_subscription(
    body: ReplaceSubscriptionArgs,
    subscription: OwnedSubscriptionDep,
    principal: CurrentUserDep,
    session: SessionDep,
    services: ServicesDep,
) -> ReplaceSubscriptionResult:
    """Replace a subscription, including its webhook secret."""
    data = body.data
    if data.source_id is not None and not await _source_owned(
        session, data.source_id, subscription.created_by
    ):
        raise NotFoundError(f"source {data.source_id} not found")
    subscription.channel = data.channel
    subscription.source_id = data.source_id
    subscription.kinds = sorted({kind.value for kind in data.kinds})
    subscription.url = data.url
    subscription.secret = services.vault.encrypt(data.secret) if data.secret is not None else None
    subscription.updated_by = principal.user_id
    await session.flush()
    await session.refresh(subscription)
    return ReplaceSubscriptionResult(data=Subscription.model_validate(subscription))


@router.delete("/{subscription_id}")
async def delete_subscription(
    subscription: OwnedSubscriptionDep, session: SessionDep
) -> DeleteSubscriptionResult:
    """Delete a subscription."""
    deleted = Subscription.model_validate(subscription)
    await session.delete(subscription)
    return DeleteSubscriptionResult(data=deleted)


async def _source_owned(session: AsyncSession, source_id: UUID, owner: UUID) -> bool:
    source = await session.get(db.Source, source_id)
    return source is not None and source.created_by == owner
