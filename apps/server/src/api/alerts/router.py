"""Alerts report what Watch noticed on the user's Observe sources.

An alert is *open* until acknowledged or resolved. Repeats of an unresolved alert with the same
cause count as `occurrences` instead of new alerts; acknowledging marks it as seen and it keeps
counting. Resolving closes it, and the next occurrence opens a new alert. Kinds: *changed*,
*gone*, *unavailable*, *auth_lost*, *limit*, *stale* and *schema_changed*.
"""

from typing import Annotated
from uuid import UUID

from fastapi import Depends, Query
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.alerts.schemas import (
    AcknowledgeAlertResult,
    Alert,
    AlertsSort,
    ListAlertsInput,
    ListAlertsResult,
    ResolveAlertResult,
)
from src.api.common import paginate, router
from src.api.dependencies import CurrentUserDep, SessionDep
from src.db import models as db
from src.errors import ConflictError, NotFoundError

router = router("/alerts", "alerts")

SORT = {
    AlertsSort.ID: db.Alert.id,
    AlertsSort.CREATED_AT: db.Alert.created_at,
    AlertsSort.UPDATED_AT: db.Alert.updated_at,
}


async def get_owned_alert(
    alert_id: UUID, principal: CurrentUserDep, session: SessionDep
) -> db.Alert:
    """Return an alert of the user's sources."""
    alert = await session.scalar(
        select(db.Alert)
        .join(db.Source, db.Source.id == db.Alert.source_id)
        .where(db.Alert.id == alert_id, db.Source.created_by == principal.user_id)
    )
    if alert is None:
        raise NotFoundError(f"alert {alert_id} not found")
    return alert


OwnedAlertDep = Annotated[db.Alert, Depends(get_owned_alert)]


@router.get("")
async def list_alerts(
    principal: CurrentUserDep, session: SessionDep, params: Annotated[ListAlertsInput, Query()]
) -> ListAlertsResult:
    """List alerts of the user's sources."""
    query = (
        select(db.Alert)
        .join(db.Source, db.Source.id == db.Alert.source_id)
        .where(db.Source.created_by == principal.user_id)
    )
    if params.status:
        query = query.where(db.Alert.status.in_(params.status))
    if params.source_id:
        query = query.where(db.Alert.source_id.in_(params.source_id))
    if params.kind:
        query = query.where(db.Alert.kind.in_(params.kind))
    rows, info = await paginate(
        session,
        query,
        params,
        sort=SORT[params.sort],
        direction=params.direction,
        key=db.Alert.id,
    )
    return ListAlertsResult(data=[Alert.model_validate(row) for row in rows], info=info)


@router.post("/{alert_id}/acknowledge")
async def acknowledge_alert(
    alert: OwnedAlertDep, principal: CurrentUserDep, session: SessionDep
) -> AcknowledgeAlertResult:
    """Mark an open alert as seen; a resolved alert answers 409."""
    await session.refresh(alert, with_for_update=True)
    if alert.status is db.AlertStatus.RESOLVED:
        raise ConflictError(f"alert {alert.id} is resolved")
    await _set_status(session, alert, db.AlertStatus.ACKNOWLEDGED, principal.user_id)
    return AcknowledgeAlertResult(data=Alert.model_validate(alert))


@router.post("/{alert_id}/resolve")
async def resolve_alert(
    alert: OwnedAlertDep, principal: CurrentUserDep, session: SessionDep
) -> ResolveAlertResult:
    """Close an alert; repeating is safe."""
    await session.refresh(alert, with_for_update=True)
    await _set_status(session, alert, db.AlertStatus.RESOLVED, principal.user_id)
    return ResolveAlertResult(data=Alert.model_validate(alert))


async def _set_status(
    session: AsyncSession, alert: db.Alert, target: db.AlertStatus, actor: UUID
) -> None:
    if alert.status is target:
        return
    stamp = (
        db.Alert.acknowledged_at if target is db.AlertStatus.ACKNOWLEDGED else db.Alert.resolved_at
    )
    await session.execute(
        update(db.Alert)
        .where(db.Alert.id == alert.id)
        .values({db.Alert.status: target, stamp: func.now(), db.Alert.updated_by: actor})
    )
    await session.refresh(alert)
