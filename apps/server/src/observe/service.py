"""Observe: polling of sources, versions, alerts, history, drafts and webhook delivery."""

import hashlib
import hmac
import json
import logging
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, JsonValue
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import distinct_on, insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import ObserveSettings
from src.credentials.service import audit
from src.db.engine import Sessions
from src.db.models import (
    Alert,
    AlertKind,
    AlertStatus,
    Credential,
    CredentialGrant,
    CredentialStatus,
    SecretType,
    Source,
    SourcePauseReason,
    SourceStatus,
    Subscription,
    SubscriptionChannel,
    Version,
)
from src.errors import ConflictError, InvalidRequestError, NotFoundError
from src.events import notify
from src.observe.credentials import Credentials, Vault
from src.observe.extract import ExtractError, ExtractRule, extract
from src.observe.fetch import (
    AUTH_STATUSES,
    BrowserRenderer,
    FetchError,
    FetchResult,
    HttpFetcher,
)
from src.observe.normalize import Normalization, canonical, compile_path, fingerprint, normalize
from src.observe.spec import ROOT_KEY, ChildRule, SourceSpec
from src.observe.structure import infer, structural_changes, value_changes
from src.storage.blobs import BlobStore
from src.telemetry import OBSERVE_ERRORS

logger = logging.getLogger(__name__)

ALERTS_CHANNEL = "xv_alerts"
"""Payload ``<alert_id>``: a new alert was committed."""
BLOB_PREFIX = "observe/blobs/"
WEBHOOK_TIMEOUT = 10.0
POLL_CONCURRENCY = 8
"""Resources of one poll fetched at once."""
_WEBHOOK_MAX_BYTES = 64 * 1024
_GONE_STATUSES = frozenset({404, 410})
_ALERT_CHANGES = 10
_STALE_RUNS = 2
"""Scheduled polls without a successful one after which a source is stale."""


def blob_key(digest: str) -> str:
    """Return the S3 key of the value with this fingerprint."""
    return f"{BLOB_PREFIX}{digest}"


class ResourceTask(BaseModel):
    """One resource to poll."""

    key: str
    url: str
    rule: list[str]
    """Names of the child rules from the source down to the rule of this resource."""
    depth: int


class PollStart(BaseModel):
    """A poll of a published source."""

    credential_id: UUID | None
    root: ResourceTask
    concurrency: int
    """Resources polled at once."""


class ResourceOutcome(BaseModel):
    """What polling one resource found."""

    fetched: bool
    """The resource was fetched and observed, or found gone."""
    complete: bool
    """Its children were enumerated completely, so missing ones are gone."""
    auth_failed: bool = False
    children: list[ResourceTask] = []
    alert_ids: list[UUID] = []


class PollEnd(BaseModel):
    """The end of a poll."""

    source_id: UUID
    credential_id: UUID | None
    seen: list[str]
    """Keys of every resource the poll enumerated."""
    complete: bool
    """Every resource was fetched and every enumeration was complete."""
    capped: bool
    """The poll stopped at its resource limit."""
    error: str | None


class AuthLost(BaseModel):
    """Sources paused because a credential was rejected, and their alerts."""

    paused: list[UUID]
    alert_ids: list[UUID]


class Delivery(BaseModel):
    """One alert for one webhook subscription."""

    alert_id: UUID
    subscription_id: UUID


@dataclass(frozen=True, slots=True)
class _Effective:
    max_bytes: int
    timeout: float
    max_depth: int
    max_fanout: int


@dataclass(frozen=True, slots=True)
class _Rule:
    """Extraction, normalization and children of one resource."""

    extract: ExtractRule | None
    normalize: Normalization
    children: list[tuple[list[str], ChildRule]]
    """Child rules with their paths."""


class Observe:
    """Polls sources and keeps their versions and alerts."""

    def __init__(  # noqa: PLR0913 - the collaborators of polling
        self,
        *,
        settings: ObserveSettings,
        sessions: Sessions,
        blobs: BlobStore,
        fetcher: HttpFetcher,
        renderer: BrowserRenderer,
        credentials: Credentials,
        vault: Vault,
    ) -> None:
        self._settings = settings
        self._sessions = sessions
        self._blobs = blobs
        self._fetcher = fetcher
        self._renderer = renderer
        self._credentials = credentials
        self._vault = vault

    # Polling

    async def start(self, source_id: UUID) -> PollStart | None:
        """Return the first resource of a poll; ``None`` unless the source is published."""
        async with self._sessions.begin() as session:
            raw = await session.scalar(
                update(Source)
                .where(Source.id == source_id, Source.status == SourceStatus.PUBLISHED)
                .values(last_polled_at=func.now())
                .returning(Source.spec)
            )
            if raw is None:
                return None
            spec = SourceSpec.model_validate(raw)
            if spec.credential_id is not None:
                await audit(
                    session,
                    spec.credential_id,
                    "used",
                    detail=f"{CredentialGrant.WATCH.value} poll of source {source_id}",
                )
        return PollStart(
            credential_id=spec.credential_id,
            root=ResourceTask(key=ROOT_KEY, url=spec.url, rule=[], depth=0),
            concurrency=POLL_CONCURRENCY,
        )

    async def poll(  # noqa: PLR0911 - one return per outcome of a resource
        self, source_id: UUID, task: ResourceTask
    ) -> ResourceOutcome:
        """Fetch and observe one resource and enumerate its children."""
        async with self._sessions() as session:
            source = await session.get(Source, source_id)
        if source is None or source.status is not SourceStatus.PUBLISHED:
            return ResourceOutcome(fetched=False, complete=False)
        spec = SourceSpec.model_validate(source.spec)
        limits = self._limits(spec)
        try:
            rule = _rule(spec, task.rule)
        except LookupError:
            return ResourceOutcome(fetched=False, complete=False)
        alerts: list[UUID] = []
        try:
            result = await self._fetch_authorized(source, spec, task, limits)
        except FetchError as error:
            return await self._unavailable(source_id, task, f"{error.code}: {error}")
        if result is None:
            return ResourceOutcome(fetched=False, complete=False, auth_failed=True)
        if result.status in _GONE_STATUSES:
            async with self._sessions.begin() as session:
                alerts += await self._gone(
                    session, source_id, spec.credential_id, task.key, task.url
                )
            return ResourceOutcome(fetched=True, complete=False, alert_ids=alerts)
        if not 200 <= result.status < 300:  # noqa: PLR2004
            return await self._unavailable(source_id, task, f"HTTP {result.status}")
        if result.truncated:
            async with self._sessions.begin() as session:
                alerts += await self._limit(
                    session,
                    source_id,
                    task.key,
                    "max_bytes",
                    f"{task.url} is larger than {limits.max_bytes} bytes and was not observed",
                )
            return ResourceOutcome(fetched=False, complete=False, alert_ids=alerts)
        try:
            value = normalize(extract(result, rule.extract), rule.normalize)
        except ExtractError as error:
            return await self._unavailable(source_id, task, str(error))
        alerts += await self._record(source_id, spec.credential_id, task, result, value)
        children, complete, limit_alerts = await self._enumerate(
            source_id, task, rule, value, limits
        )
        return ResourceOutcome(
            fetched=True, complete=complete, children=children, alert_ids=alerts + limit_alerts
        )

    async def finish(self, end: PollEnd) -> list[UUID]:
        """Record the poll; after a complete one, resources no longer enumerated are gone."""
        alerts: list[UUID] = []
        async with self._sessions.begin() as session:
            values: dict[str, object] = {"last_error": end.error}
            if end.error is None:
                values["last_success_at"] = func.now()
            found = await session.scalar(
                update(Source).where(Source.id == end.source_id).values(values).returning(Source.id)
            )
            if found is None:
                return []
            if end.error is None:
                await _resolve(session, end.source_id, [AlertKind.STALE], ROOT_KEY)
            if end.capped:
                alerts += await self._limit(
                    session,
                    end.source_id,
                    ROOT_KEY,
                    "max_resources",
                    f"the poll stopped after {len(end.seen)} resources",
                )
            if end.complete:
                seen = set(end.seen)
                for key, url in await _present(session, end.source_id, end.credential_id):
                    if key not in seen:
                        alerts += await self._gone(
                            session, end.source_id, end.credential_id, key, url
                        )
        return alerts

    async def auth_lost(self, source_id: UUID) -> AuthLost:
        """Reject the source's credential and pause every published source that uses it."""
        async with self._sessions() as session:
            source = await session.get_one(Source, source_id)
        credential_id = SourceSpec.model_validate(source.spec).credential_id
        if credential_id is None:
            return AuthLost(paused=[], alert_ids=[])
        await self._credentials.reject(credential_id)
        alerts: list[UUID] = []
        async with self._sessions.begin() as session:
            paused = (
                await session.scalars(
                    update(Source)
                    .where(
                        Source.credential_id == credential_id,
                        Source.status == SourceStatus.PUBLISHED,
                    )
                    .values(status=SourceStatus.PAUSED, pause_reason=SourcePauseReason.AUTH_LOST)
                    .returning(Source.id)
                )
            ).all()
            for paused_id in paused:
                alert = await raise_alert(
                    session,
                    paused_id,
                    AlertKind.AUTH_LOST,
                    ROOT_KEY,
                    f"credential {credential_id} was rejected; the source is paused until the "
                    "credential is updated",
                    data={"credential_id": str(credential_id)},
                )
                alerts += [alert] if alert else []
        return AuthLost(paused=list(paused), alert_ids=alerts)

    async def published(self) -> list[UUID]:
        """Return the ids of the published sources."""
        async with self._sessions() as session:
            rows = await session.scalars(
                select(Source.id).where(Source.status == SourceStatus.PUBLISHED).order_by(Source.id)
            )
            return list(rows.all())

    async def stale(self, source_id: UUID, scheduled: Sequence[datetime]) -> UUID | None:
        """Raise ``stale`` when scheduled polls passed without a successful one."""
        async with self._sessions.begin() as session:
            source = await session.get(Source, source_id)
            if source is None or source.status is not SourceStatus.PUBLISHED:
                return None
            since = source.last_success_at or source.updated_at
            missed = [at for at in scheduled if at > since]
            if len(missed) < _STALE_RUNS:
                return None
            return await raise_alert(
                session,
                source_id,
                AlertKind.STALE,
                ROOT_KEY,
                f"no successful poll since {since.isoformat()}; "
                f"{len(missed)} scheduled polls missed",
                data={"since": since.isoformat(), "missed": len(missed)},
            )

    def _limits(self, spec: SourceSpec) -> _Effective:
        settings, limits = self._settings, spec.limits

        def capped[T: (int, float)](value: T | None, maximum: T) -> T:
            return maximum if value is None else min(value, maximum)

        return _Effective(
            max_bytes=capped(limits.max_bytes, int(settings.max_response_bytes)),
            timeout=capped(limits.timeout_s, settings.fetch_timeout.total_seconds()),
            max_depth=capped(limits.max_depth, settings.max_depth),
            max_fanout=capped(limits.max_fanout, settings.max_fanout),
        )

    async def _fetch_authorized(
        self, source: Source, spec: SourceSpec, task: ResourceTask, limits: _Effective
    ) -> FetchResult | None:
        """Fetch, renewing a rejected credential once.

        ``None`` means the credential is still rejected, or revoked, expired, not granted to
        Watch, out of the source's scope, or not allowed for the host of this resource.
        """
        result = await self._fetch(source, spec, task, limits)
        if result is None or result.status not in AUTH_STATUSES or spec.credential_id is None:
            return result
        if not await self._credentials.refresh(
            spec.credential_id, owner=source.created_by, chat_id=source.chat_id
        ):
            return None
        result = await self._fetch(source, spec, task, limits)
        return None if result is None or result.status in AUTH_STATUSES else result

    async def _fetch(
        self, source: Source, spec: SourceSpec, task: ResourceTask, limits: _Effective
    ) -> FetchResult | None:
        credential: dict[str, str] | None = {}
        if spec.credential_id is not None:
            credential = await self._credentials.headers(
                spec.credential_id, owner=source.created_by, chat_id=source.chat_id, url=task.url
            )
            if credential is None:
                return None
        if spec.mode == "browser":
            return await self._renderer.render(
                task.url,
                headers=spec.headers,
                credential=credential,
                max_bytes=limits.max_bytes,
                within=limits.timeout,
                wait_s=spec.render_wait_s,
            )
        root = task.key == ROOT_KEY
        return await self._fetcher.fetch(
            task.url,
            method=spec.method if root else "GET",
            headers=spec.headers,
            credential=credential,
            content=spec.body.encode() if root and spec.body is not None else None,
            max_bytes=limits.max_bytes,
            within=limits.timeout,
        )

    async def _unavailable(
        self, source_id: UUID, task: ResourceTask, reason: str
    ) -> ResourceOutcome:
        OBSERVE_ERRORS.add(1)
        async with self._sessions.begin() as session:
            alert = await raise_alert(
                session,
                source_id,
                AlertKind.UNAVAILABLE,
                task.key,
                f"{task.url} is unavailable: {reason}",
                data={"url": task.url, "reason": reason},
            )
        return ResourceOutcome(fetched=False, complete=False, alert_ids=[alert] if alert else [])

    async def _record(
        self,
        source_id: UUID,
        credential_id: UUID | None,
        task: ResourceTask,
        result: FetchResult,
        value: JsonValue,
    ) -> list[UUID]:
        """Store a version when the fingerprint changed and raise its alerts."""
        digest = fingerprint(value)
        async with self._sessions.begin() as session:
            await _resolve(session, source_id, [AlertKind.UNAVAILABLE, AlertKind.GONE], task.key)
            previous = await _latest(session, source_id, credential_id, task.key)
        if previous is not None and previous.fingerprint == digest:
            return []
        body = canonical(value)
        key = blob_key(digest)
        if not await self._blobs.exists(key):
            await self._blobs.put(key, body, content_type="application/json")
        structure = infer(value)
        schema_changes: list[JsonValue] = []
        changes: list[JsonValue] = []
        total = 0
        if previous is not None and previous.fingerprint is not None:
            if isinstance(previous.structure, dict):
                schema_changes = structural_changes(previous.structure, structure)
            old = json.loads(await self._blobs.get(blob_key(previous.fingerprint)))
            changes, total = value_changes(old, value)
        version = Version(
            id=uuid.uuid7(),
            source_id=source_id,
            resource_key=task.key,
            credential_id=credential_id,
            url=task.url,
            fingerprint=digest,
            content_type=result.content_type,
            size_bytes=len(body),
            structure=structure,
            schema_changes=list(schema_changes),
            changes=list(changes),
            change_count=total,
        )
        alerts: list[UUID | None] = []
        async with self._sessions.begin() as session:
            session.add(version)
            await session.flush()
            if previous is not None:
                alerts.append(
                    await raise_alert(
                        session,
                        source_id,
                        AlertKind.CHANGED,
                        task.key,
                        f"{task.url} changed: {total} value changes",
                        data={
                            "url": task.url,
                            "change_count": total,
                            "changes": changes[:_ALERT_CHANGES],
                        },
                        version_id=version.id,
                    )
                )
            if schema_changes:
                alerts.append(
                    await raise_alert(
                        session,
                        source_id,
                        AlertKind.SCHEMA_CHANGED,
                        task.key,
                        f"the structure of {task.url} changed: {len(schema_changes)} differences",
                        data={"url": task.url, "schema_changes": schema_changes[:_ALERT_CHANGES]},
                        version_id=version.id,
                    )
                )
        return [alert for alert in alerts if alert is not None]

    async def _enumerate(
        self,
        source_id: UUID,
        task: ResourceTask,
        rule: _Rule,
        value: JsonValue,
        limits: _Effective,
    ) -> tuple[list[ResourceTask], bool, list[UUID]]:
        """Return the children of a resource, whether the enumeration is complete, and alerts."""
        children: list[ResourceTask] = []
        complete = True
        alerts: list[UUID] = []
        async with self._sessions.begin() as session:
            for path, child in rule.children:
                if task.depth + 1 > limits.max_depth:
                    complete = False
                    alerts += await self._limit(
                        session,
                        source_id,
                        task.key,
                        "max_depth",
                        f"children of {task.url} are below the depth limit {limits.max_depth}",
                    )
                    continue
                matches = [m.value for m in compile_path(child.select).find(value)]
                values = list(dict.fromkeys(v for v in matches if _is_key(v)))
                if len(values) != len(matches):
                    complete = False
                declared = (
                    [m.value for m in compile_path(child.total).find(value)] if child.total else []
                )
                if declared and declared[0] != len(values):
                    complete = False
                    alerts += await self._limit(
                        session,
                        source_id,
                        task.key,
                        f"incomplete:{child.name}",
                        f"{task.url} declares {declared[0]} `{child.name}` children but "
                        f"enumerates {len(values)}",
                    )
                if len(values) > limits.max_fanout:
                    complete = False
                    alerts += await self._limit(
                        session,
                        source_id,
                        task.key,
                        f"max_fanout:{child.name}",
                        f"{task.url} has {len(values)} `{child.name}` children; only the first "
                        f"{limits.max_fanout} are polled",
                    )
                    values = values[: limits.max_fanout]
                children += [
                    ResourceTask(
                        key=child.key(v), url=child.child_url(v), rule=path, depth=task.depth + 1
                    )
                    for v in values
                ]
        return children, complete, alerts

    async def _gone(
        self,
        session: AsyncSession,
        source_id: UUID,
        credential_id: UUID | None,
        key: str,
        url: str,
    ) -> list[UUID]:
        """Record that a resource is gone, once."""
        previous = await _latest(session, source_id, credential_id, key)
        if previous is None or previous.fingerprint is None:
            return []
        version = Version(
            id=uuid.uuid7(),
            source_id=source_id,
            resource_key=key,
            credential_id=credential_id,
            url=url,
            fingerprint=None,
            content_type="",
            size_bytes=0,
            structure=None,
            schema_changes=[],
            changes=[],
            change_count=0,
        )
        session.add(version)
        await session.flush()
        alert = await raise_alert(
            session,
            source_id,
            AlertKind.GONE,
            key,
            f"{url} is gone",
            data={"url": url},
            version_id=version.id,
        )
        return [alert] if alert else []

    async def _limit(
        self, session: AsyncSession, source_id: UUID, key: str, limit: str, message: str
    ) -> list[UUID]:
        alert = await raise_alert(
            session, source_id, AlertKind.LIMIT, key, message, data={"limit": limit}, dedup=limit
        )
        return [alert] if alert else []

    # Webhooks

    async def deliveries(self, alert_ids: Sequence[UUID]) -> list[Delivery]:
        """Return the webhook deliveries of new alerts."""
        if not alert_ids:
            return []
        async with self._sessions() as session:
            rows = await session.execute(
                select(Alert.id, Subscription.id)
                .join(Source, Source.id == Alert.source_id)
                .join(
                    Subscription,
                    (Subscription.channel == SubscriptionChannel.WEBHOOK)
                    & (Subscription.created_by == Source.created_by)
                    & (
                        Subscription.source_id.is_(None)
                        | (Subscription.source_id == Alert.source_id)
                    )
                    & (
                        (func.cardinality(Subscription.kinds) == 0)
                        | Alert.kind.op("=")(func.any(Subscription.kinds))
                    ),
                )
                .where(Alert.id.in_(alert_ids))
                .order_by(Alert.id, Subscription.id)
            )
            return [Delivery(alert_id=a, subscription_id=s) for a, s in rows]

    async def deliver(self, delivery: Delivery) -> None:
        """POST the alert to the webhook, signed with its secret.

        The body is the alert as JSON. ``X-Xavier-Signature: t=<unix time>,v1=<hex>`` carries the
        HMAC-SHA256 of ``<unix time>.<body>``.

        Raises
        ------
        FetchError
            If the webhook did not accept the alert; the delivery is retried.
        """
        async with self._sessions() as session:
            alert = await session.get(Alert, delivery.alert_id)
            subscription = await session.get(Subscription, delivery.subscription_id)
        if alert is None or subscription is None:
            return
        url, sealed = subscription.url, subscription.secret
        if url is None or sealed is None:
            raise ValueError(f"subscription {subscription.id} is not a webhook")
        body = json.dumps(alert_payload(alert), separators=(",", ":")).encode()
        timestamp = str(int(time.time()))
        secret = self._vault.decrypt(sealed)
        signature = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256)
        result = await self._fetcher.fetch(
            url,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-Xavier-Signature": f"t={timestamp},v1={signature.hexdigest()}",
            },
            content=body,
            max_bytes=_WEBHOOK_MAX_BYTES,
            within=WEBHOOK_TIMEOUT,
        )
        if not 200 <= result.status < 300:  # noqa: PLR2004
            raise FetchError("failed", f"the webhook answered HTTP {result.status}")


class Sources:
    """The catalog of sources: drafts, publication, versions and diffs."""

    def __init__(self, sessions: Sessions, blobs: BlobStore) -> None:
        self._sessions = sessions
        self._blobs = blobs

    # History

    async def latest(
        self, source_id: UUID, owner: UUID, key: str = ROOT_KEY
    ) -> tuple[Version, JsonValue]:
        """Return the latest version of a resource under the source's credential and its value.

        Raises
        ------
        NotFoundError
            If the owner has no such source or the resource has no versions.
        """
        async with self._sessions() as session:
            source = await _source(session, source_id, owner)
            version = await _latest(session, source_id, source.credential_id, key)
        if version is None:
            raise NotFoundError(f"source {source_id} has no versions of {key or 'its URL'}")
        return version, await self.value(version)

    async def versions(
        self, source_id: UUID, owner: UUID, key: str | None, cursor: UUID | None, limit: int
    ) -> list[Version]:
        """Return versions under the source's credential, newest first."""
        async with self._sessions() as session:
            source = await _source(session, source_id, owner)
            query = select(Version).where(
                Version.source_id == source_id,
                Version.credential_id.is_not_distinct_from(source.credential_id),
            )
            if key is not None:
                query = query.where(Version.resource_key == key)
            if cursor is not None:
                query = query.where(Version.id < cursor)
            return list(
                (await session.scalars(query.order_by(Version.id.desc()).limit(limit))).all()
            )

    async def version(self, source_id: UUID, owner: UUID, version_id: UUID) -> Version:
        """Return a version of the source.

        Raises
        ------
        NotFoundError
            If the owner has no such source or the source has no such version.
        """
        async with self._sessions() as session:
            await _source(session, source_id, owner)
            version = await session.get(Version, version_id)
        if version is None or version.source_id != source_id:
            raise NotFoundError(f"version {version_id} of source {source_id} not found")
        return version

    async def value(self, version: Version) -> JsonValue:
        """Return the observed value of a version; ``None`` when the resource was gone."""
        if version.fingerprint is None:
            return None
        return json.loads(await self._blobs.get(blob_key(version.fingerprint)))

    async def diff(
        self, source_id: UUID, owner: UUID, older: UUID, newer: UUID
    ) -> dict[str, JsonValue]:
        """Return the value and structure changes between two versions of one resource.

        Raises
        ------
        NotFoundError
            If a version does not belong to the source.
        InvalidRequestError
            If the versions are of different resources or credentials.
        """
        first = await self.version(source_id, owner, older)
        second = await self.version(source_id, owner, newer)
        if (first.resource_key, first.credential_id) != (second.resource_key, second.credential_id):
            raise InvalidRequestError("the versions belong to different resources or credentials")
        changes, total = value_changes(await self.value(first), await self.value(second))
        schema_changes: list[JsonValue] = []
        if isinstance(first.structure, dict) and isinstance(second.structure, dict):
            schema_changes = structural_changes(first.structure, second.structure)
        return {
            "from_version": str(first.id),
            "to_version": str(second.id),
            "resource_key": first.resource_key,
            "change_count": total,
            "changes": list(changes),
            "schema_changes": list(schema_changes),
        }

    # Drafts and publication

    async def draft(self, name: str, spec: SourceSpec, chat_id: UUID | None, owner: UUID) -> Source:
        """Create a draft source of ``owner``."""
        async with self._sessions.begin() as session:
            await _check_credential(session, spec.credential_id, owner, chat_id)
            source = Source(
                id=uuid.uuid7(),
                created_by=owner,
                name=name,
                status=SourceStatus.DRAFT,
                spec=spec.model_dump(mode="json"),
                credential_id=spec.credential_id,
                chat_id=chat_id,
                updated_by=owner,
            )
            session.add(source)
            await session.flush()
            await session.refresh(source)
        return source

    async def source(self, source_id: UUID, owner: UUID) -> Source:
        """Return a source of ``owner``.

        Raises
        ------
        NotFoundError
            If the owner has no such source.
        """
        async with self._sessions() as session:
            return await _source(session, source_id, owner)

    async def revise(self, source_id: UUID, name: str, spec: SourceSpec, owner: UUID) -> Source:
        """Replace a draft's specification, or propose a revision of a published source."""
        async with self._sessions.begin() as session:
            source = await _source(session, source_id, owner, lock=True)
            await _check_credential(session, spec.credential_id, owner, source.chat_id)
            source.name = name
            if source.status is SourceStatus.DRAFT:
                source.spec = spec.model_dump(mode="json")
                source.credential_id = spec.credential_id
            else:
                source.draft = spec.model_dump(mode="json")
            source.updated_by = owner
            await session.flush()
            await session.refresh(source)
        return source

    async def publish(self, source_id: UUID, owner: UUID) -> Source:
        """Apply the pending revision and start polling.

        Raises
        ------
        NotFoundError
            If the owner has no such source or credential.
        """
        async with self._sessions.begin() as session:
            source = await _source(session, source_id, owner, lock=True)
            if source.draft is not None:
                source.spec, source.draft = source.draft, None
            spec = SourceSpec.model_validate(source.spec)
            await _check_credential(session, spec.credential_id, owner, source.chat_id)
            source.credential_id = spec.credential_id
            source.status = SourceStatus.PUBLISHED
            source.pause_reason = None
            source.updated_by = owner
            await session.flush()
            await session.refresh(source)
        return source

    async def pause(self, source_id: UUID, owner: UUID, reason: SourcePauseReason) -> Source:
        """Stop polling a published source.

        Raises
        ------
        ConflictError
            If the source is a draft.
        """
        async with self._sessions.begin() as session:
            source = await _source(session, source_id, owner, lock=True)
            if source.status is SourceStatus.DRAFT:
                raise ConflictError("a draft is not polled; publish it first")
            source.status = SourceStatus.PAUSED
            source.pause_reason = reason
            if reason is SourcePauseReason.OPERATOR:
                source.updated_by = owner
            await session.flush()
            await session.refresh(source)
        return source

    async def resume(self, source_id: UUID, owner: UUID) -> Source:
        """Resume polling a paused source and resolve its ``auth_lost`` alert.

        Raises
        ------
        ConflictError
            If the source is a draft.
        """
        async with self._sessions.begin() as session:
            source = await _source(session, source_id, owner, lock=True)
            if source.status is SourceStatus.DRAFT:
                raise ConflictError("a draft is not polled; publish it first")
            source.status = SourceStatus.PUBLISHED
            source.pause_reason = None
            source.updated_by = owner
            await _resolve(session, source_id, [AlertKind.AUTH_LOST], ROOT_KEY)
            await session.flush()
            await session.refresh(source)
        return source

    async def credential_replaced(self, credential_id: UUID) -> list[UUID]:
        """Resume the sources paused because the credential was rejected; return their ids."""
        async with self._sessions.begin() as session:
            resumed = (
                await session.scalars(
                    update(Source)
                    .where(
                        Source.credential_id == credential_id,
                        Source.pause_reason == SourcePauseReason.AUTH_LOST,
                    )
                    .values(status=SourceStatus.PUBLISHED, pause_reason=None)
                    .returning(Source.id)
                )
            ).all()
            for source_id in resumed:
                await _resolve(session, source_id, [AlertKind.AUTH_LOST], ROOT_KEY)
        return list(resumed)

    async def delete(self, source_id: UUID, owner: UUID) -> Source:
        """Delete the source with its versions and alerts, and the values no source refers to.

        Returns the deleted source.
        """
        async with self._sessions.begin() as session:
            source = await _source(session, source_id, owner, lock=True)
            digests = set(
                (
                    await session.scalars(
                        select(Version.fingerprint)
                        .where(Version.source_id == source_id, Version.fingerprint.is_not(None))
                        .distinct()
                    )
                ).all()
            )
            await session.delete(source)
            await session.flush()
            shared = set(
                (
                    await session.scalars(
                        select(Version.fingerprint).where(Version.fingerprint.in_(digests))
                    )
                ).all()
            )
        unused = sorted(digest for digest in digests - shared if digest)
        await self._blobs.delete([blob_key(digest) for digest in unused])
        return source


def alert_payload(alert: Alert) -> dict[str, JsonValue]:
    """Return the alert as delivered to subscribers."""
    return {
        "id": str(alert.id),
        "source_id": str(alert.source_id),
        "kind": alert.kind.value,
        "status": alert.status.value,
        "resource_key": alert.resource_key,
        "message": alert.message,
        "data": alert.data,
        "version_id": str(alert.version_id) if alert.version_id else None,
        "occurrences": alert.occurrences,
        "created_at": alert.created_at.isoformat(),
    }


async def raise_alert(  # noqa: PLR0913 - the fields of an alert
    session: AsyncSession,
    source_id: UUID,
    kind: AlertKind,
    key: str,
    message: str,
    *,
    data: JsonValue,
    version_id: UUID | None = None,
    dedup: str = "",
) -> UUID | None:
    """Open an alert, or count a repeat of the unresolved one with the same key.

    Returns the id of a new alert; ``None`` for a repeat.
    """
    dedup_key = f"{kind.value}:{source_id}:{key}:{dedup}"
    row = (
        await session.execute(
            insert(Alert)
            .values(
                id=uuid.uuid7(),
                source_id=source_id,
                kind=kind,
                status=AlertStatus.OPEN,
                dedup_key=dedup_key,
                resource_key=key,
                message=message,
                data=data,
                version_id=version_id,
                occurrences=1,
            )
            .on_conflict_do_update(
                index_elements=[Alert.dedup_key],
                index_where=Alert.status != AlertStatus.RESOLVED,
                set_={
                    "occurrences": Alert.occurrences + 1,
                    "message": message,
                    "data": data,
                    "version_id": version_id,
                    "updated_at": func.now(),
                },
            )
            .returning(Alert.id, Alert.occurrences)
        )
    ).one()
    if row.occurrences > 1:
        return None
    await notify(session, ALERTS_CHANNEL, str(row.id))
    return row.id


async def _resolve(
    session: AsyncSession, source_id: UUID, kinds: Sequence[AlertKind], key: str
) -> None:
    await session.execute(
        update(Alert)
        .where(
            Alert.source_id == source_id,
            Alert.kind.in_(kinds),
            Alert.resource_key == key,
            Alert.status != AlertStatus.RESOLVED,
        )
        .values(status=AlertStatus.RESOLVED, resolved_at=func.now())
    )


async def _latest(
    session: AsyncSession, source_id: UUID, credential_id: UUID | None, key: str
) -> Version | None:
    return await session.scalar(
        select(Version)
        .where(
            Version.source_id == source_id,
            Version.resource_key == key,
            Version.credential_id.is_not_distinct_from(credential_id),
        )
        .order_by(Version.id.desc())
        .limit(1)
    )


async def _present(
    session: AsyncSession, source_id: UUID, credential_id: UUID | None
) -> list[tuple[str, str]]:
    """Return the key and URL of every child resource whose latest version is not gone."""
    latest = (
        select(Version.resource_key, Version.url, Version.fingerprint)
        .where(
            Version.source_id == source_id,
            Version.resource_key != ROOT_KEY,
            Version.credential_id.is_not_distinct_from(credential_id),
        )
        .ext(distinct_on(Version.resource_key))
        .order_by(Version.resource_key, Version.id.desc())
        .subquery()
    )
    rows = await session.execute(
        select(latest.c.resource_key, latest.c.url).where(latest.c.fingerprint.is_not(None))
    )
    return [(key, url) for key, url in rows]


async def _source(
    session: AsyncSession, source_id: UUID, owner: UUID | None, *, lock: bool = False
) -> Source:
    """Return the source; ``owner`` ``None`` is the system, which sees every source."""
    source = await session.get(Source, source_id, with_for_update=lock)
    if source is None or owner not in {None, source.created_by}:
        raise NotFoundError(f"source {source_id} not found")
    return source


async def _check_credential(
    session: AsyncSession, credential_id: UUID | None, owner: UUID, chat_id: UUID | None
) -> None:
    """Check that the owner's credential exists and may be polled with.

    Raises
    ------
    NotFoundError
        If the owner has no such credential.
    InvalidRequestError
        If it is not an active token granted to Watch in the source's scope.
    """
    if credential_id is None:
        return
    credential = await session.get(Credential, credential_id)
    if credential is None or credential.created_by != owner:
        raise NotFoundError(f"credential {credential_id} not found")
    if credential.status is not CredentialStatus.ACTIVE:
        raise InvalidRequestError(f"credential {credential_id} is revoked")
    if not credential.grant_watch:
        raise InvalidRequestError(f"credential {credential_id} is not granted to Watch")
    if credential.secret_type is not SecretType.TOKEN:
        raise InvalidRequestError(f"credential {credential_id} is a login, not a token")
    if credential.chat_id is not None and credential.chat_id != chat_id:
        raise InvalidRequestError(f"credential {credential_id} belongs to another chat")


def _rule(spec: SourceSpec, path: Sequence[str]) -> _Rule:
    """Return the rule of the resource at ``path``; a recursive rule also applies to its children.

    Raises
    ------
    LookupError
        If the specification has no rule at ``path``, as after a revision.
    """
    if not path:
        return _Rule(spec.extract, spec.normalize, [([c.name], c) for c in spec.children])
    current = _named(spec.children, path[0])
    for name in path[1:]:
        current = _named(current.children, name)
    children = [([*path, c.name], c) for c in current.children]
    if current.recursive:
        children.append((list(path), current))
    return _Rule(current.extract, current.normalize, children)


def _named(rules: Sequence[ChildRule], name: str) -> ChildRule:
    for rule in rules:
        if rule.name == name:
            return rule
    raise LookupError(name)


def _is_key(value: object) -> bool:
    return isinstance(value, (str, int, float)) and not isinstance(value, bool) and value != ""
