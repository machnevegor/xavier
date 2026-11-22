"""Observe sources are URLs that Watch polls on a schedule, keeping a version of every change.

A source starts as a **draft**: nothing is polled until it is published. Publishing approves
everything listed in `widening`, such as new hosts, the credential and higher limits, and starts
polling on the source's schedule. A revision of a published source is applied when it is
replaced. A paused source keeps its versions and alerts; a source whose credential was rejected
pauses with an `auth_lost` alert until the credential's secret is replaced.

Every distinct observed value of a resource is a version: `""` is the source URL and
`<rule>:<value>` a child resource. Versions are kept per credential, and lists show those of the
source's current credential. Polls send the credential only to its hosts, child resources
included.
"""

from typing import Annotated
from uuid import UUID

from fastapi import Depends, Query, status
from sqlalchemy import select

from src.api.common import CONFLICT, INVALID_REQUEST, NOT_FOUND, UNAVAILABLE, paginate, router
from src.api.dependencies import CurrentUserDep, Services, ServicesDep, SessionDep
from src.api.sources.schemas import (
    CheckSourceResult,
    CreateSourceArgs,
    CreateSourceResult,
    DeleteSourceResult,
    Diff,
    DiffVersionsInput,
    DiffVersionsResult,
    ListSourcesInput,
    ListSourcesResult,
    ListVersionsInput,
    ListVersionsResult,
    PauseSourceResult,
    PublishSourceResult,
    ReadSourceResult,
    ReadVersionResult,
    ReplaceSourceArgs,
    ReplaceSourceResult,
    ResumeSourceResult,
    Source,
    SourcesSort,
    Version,
    VersionsSort,
    VersionValue,
)
from src.db import models as db
from src.errors import ConflictError, NotFoundError
from src.observe.spec import SourceSpec, widening

router = router("/sources", "sources")

SORT = {
    SourcesSort.ID: db.Source.id,
    SourcesSort.NAME: db.Source.name,
    SourcesSort.CREATED_AT: db.Source.created_at,
    SourcesSort.UPDATED_AT: db.Source.updated_at,
}
VERSIONS_SORT = {
    VersionsSort.ID: db.Version.id,
    VersionsSort.CREATED_AT: db.Version.created_at,
}


async def get_owned_source(
    source_id: UUID, principal: CurrentUserDep, session: SessionDep
) -> db.Source:
    """Return the user's source."""
    source = await session.get(db.Source, source_id)
    if source is None or source.created_by != principal.user_id:
        raise NotFoundError(f"source {source_id} not found")
    return source


OwnedSourceDep = Annotated[db.Source, Depends(get_owned_source)]


@router.get("")
async def list_sources(
    principal: CurrentUserDep, session: SessionDep, params: Annotated[ListSourcesInput, Query()]
) -> ListSourcesResult:
    """List the user's sources."""
    query = select(db.Source).where(db.Source.created_by == principal.user_id)
    if params.status:
        query = query.where(db.Source.status.in_(params.status))
    if params.chat_id:
        query = query.where(db.Source.chat_id.in_(params.chat_id))
    rows, info = await paginate(
        session,
        query,
        params,
        sort=SORT[params.sort],
        direction=params.direction,
        key=db.Source.id,
    )
    return ListSourcesResult(data=[_out(row) for row in rows], info=info)


@router.post(
    "", status_code=status.HTTP_201_CREATED, responses={404: NOT_FOUND, 422: INVALID_REQUEST}
)
async def create_source(
    body: CreateSourceArgs, principal: CurrentUserDep, services: ServicesDep
) -> CreateSourceResult:
    """Create a draft source.

    A `spec.credential_id` must name an active token of the user granted to Watch.
    """
    source = await services.sources.draft(body.data.name, body.data.spec, None, principal.user_id)
    return CreateSourceResult(data=_out(source))


@router.get("/{source_id}")
async def read_source(source: OwnedSourceDep) -> ReadSourceResult:
    """Return a source with its pending revision and what publishing it would widen."""
    return ReadSourceResult(data=_out(source))


@router.put("/{source_id}", responses={422: INVALID_REQUEST, 503: UNAVAILABLE})
async def replace_source(
    body: ReplaceSourceArgs, source: OwnedSourceDep, services: ServicesDep
) -> ReplaceSourceResult:
    """Replace the name and specification; a published or paused source is republished."""
    revised = await services.sources.revise(
        source.id, body.data.name, body.data.spec, source.created_by
    )
    if revised.status is not db.SourceStatus.DRAFT:
        revised = await _publish(services, source.id, source.created_by)
    return ReplaceSourceResult(data=_out(revised))


@router.delete("/{source_id}", responses={503: UNAVAILABLE})
async def delete_source(source: OwnedSourceDep, services: ServicesDep) -> DeleteSourceResult:
    """Delete the source with its versions, alerts and subscriptions."""
    await services.source_schedules.delete(source.id)
    deleted = await services.sources.delete(source.id, source.created_by)
    return DeleteSourceResult(data=_out(deleted))


@router.post("/{source_id}/publish", responses={422: INVALID_REQUEST, 503: UNAVAILABLE})
async def publish_source(source: OwnedSourceDep, services: ServicesDep) -> PublishSourceResult:
    """Confirm the draft or the pending revision and start polling on its schedule.

    Publishing a polled source without a revision only resumes it.
    """
    published = await _publish(services, source.id, source.created_by)
    return PublishSourceResult(data=_out(published))


@router.post("/{source_id}/pause", responses={409: CONFLICT, 503: UNAVAILABLE})
async def pause_source(source: OwnedSourceDep, services: ServicesDep) -> PauseSourceResult:
    """Stop polling a published source; a draft answers 409."""
    paused = await services.sources.pause(
        source.id, source.created_by, db.SourcePauseReason.OPERATOR
    )
    await services.source_schedules.pause(source.id, "paused by the operator")
    return PauseSourceResult(data=_out(paused))


@router.post("/{source_id}/resume", responses={409: CONFLICT, 503: UNAVAILABLE})
async def resume_source(source: OwnedSourceDep, services: ServicesDep) -> ResumeSourceResult:
    """Resume polling a paused source and resolve its `auth_lost` alert; a draft answers 409."""
    resumed = await services.sources.resume(source.id, source.created_by)
    await services.source_schedules.resume(source.id)
    return ResumeSourceResult(data=_out(resumed))


@router.post("/{source_id}/check", responses={503: UNAVAILABLE})
async def check_source(source: OwnedSourceDep, services: ServicesDep) -> CheckSourceResult:
    """Poll a published source now, outside its schedule. Skipped while a poll runs."""
    if source.status is not db.SourceStatus.PUBLISHED:
        raise ConflictError(
            f"source {source.id} is not published; only a published source is polled"
        )
    await services.source_schedules.trigger(source.id)
    return CheckSourceResult(data=_out(source))


@router.get("/{source_id}/versions")
async def list_versions(
    source: OwnedSourceDep, session: SessionDep, params: Annotated[ListVersionsInput, Query()]
) -> ListVersionsResult:
    """List the versions observed with the source's current credential."""
    query = select(db.Version).where(
        db.Version.source_id == source.id,
        db.Version.credential_id.is_not_distinct_from(source.credential_id),
    )
    if params.resource_key is not None:
        query = query.where(db.Version.resource_key == params.resource_key)
    rows, info = await paginate(
        session,
        query,
        params,
        sort=VERSIONS_SORT[params.sort],
        direction=params.direction,
        key=db.Version.id,
    )
    return ListVersionsResult(data=[Version.model_validate(row) for row in rows], info=info)


@router.get("/{source_id}/versions/{version_id}")
async def read_version(
    version_id: UUID, source: OwnedSourceDep, services: ServicesDep
) -> ReadVersionResult:
    """Return a version with its normalized observed value."""
    version = await services.sources.version(source.id, source.created_by, version_id)
    value = await services.sources.value(version)
    return ReadVersionResult(
        data=VersionValue.model_validate(version).model_copy(update={"value": value})
    )


@router.get("/{source_id}/diff", responses={422: INVALID_REQUEST})
async def diff_versions(
    source: OwnedSourceDep, services: ServicesDep, params: Annotated[DiffVersionsInput, Query()]
) -> DiffVersionsResult:
    """Return the value and structure changes between two versions of one resource.

    Both versions must belong to the same resource and credential.
    """
    diff = await services.sources.diff(
        source.id, source.created_by, params.from_version, params.to_version
    )
    return DiffVersionsResult(data=Diff.model_validate(diff))


async def _publish(services: Services, source_id: UUID, owner: UUID) -> db.Source:
    source = await services.sources.publish(source_id, owner)
    spec = SourceSpec.model_validate(source.spec)
    await services.source_schedules.put(source_id, spec.schedule, paused=False)
    return source


def _out(source: db.Source) -> Source:
    """Return the source with what publishing its pending revision would widen."""
    polled = SourceSpec.model_validate(source.spec)
    if source.status is db.SourceStatus.DRAFT:
        widened = widening(None, polled)
    elif source.draft is not None:
        widened = widening(polled, SourceSpec.model_validate(source.draft))
    else:
        widened = []
    return Source.model_validate(source).model_copy(update={"widening": widened})
