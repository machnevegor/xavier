"""Credentials are secrets the agent and Watch send to services on the user's behalf.

A credential is a **token**, sent in a request header as `header: prefix + value`, or a
**login**, a username and a password typed into login forms of the Computer's browser. The
secret is encrypted at rest and never returned: responses, events and the agent see only the
credential's id, name, hosts and grants.

Every use is bounded by:

- `hosts`: host patterns (`example.com`, `*.example.com`) the value may be sent to; Watch checks
  them for every polled URL, child resources included;
- `grants`: `agent` lets tools use it, `watch` lets Observe polls send a token, and `env` names
  an environment variable of the chat's `code_run` executions; the executed code can read that
  value and send it anywhere;
- `chat_id`: only that chat may use it; a global credential (`chat_id` *null*) serves every chat
  of the user;
- `status` and `expires_at`: a revoked or expired credential is never used.

Two active credentials may not grant the same environment variable in overlapping scopes; a
global credential overlaps every chat. A token's `refresh_url` must be HTTPS on one of its
hosts. Every change and every use is recorded in the audit trail, which outlives the credential.
"""

import uuid
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Query, status
from sqlalchemy import ColumnElement, exists, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from src.agent.gateway import Actions
from src.api.actions.schemas import Action
from src.api.common import UNAVAILABLE, paginate, router
from src.api.credentials.schemas import (
    AnswerCredentialRequestArgs,
    AnswerCredentialRequestInput,
    AnswerCredentialRequestResult,
    AnswerTokenCredentialRequestInput,
    CreateCredentialArgs,
    CreateCredentialResult,
    CreateTokenCredentialInput,
    CredentialEvent,
    CredentialEventsSort,
    CredentialsSort,
    DeleteCredentialResult,
    ListCredentialEventsInput,
    ListCredentialEventsResult,
    ListCredentialsInput,
    ListCredentialsResult,
    LoginCredential,
    ReadCredentialResult,
    RevokeCredentialResult,
    TokenCredential,
    UpdateCredentialArgs,
    UpdateCredentialInput,
    UpdateCredentialResult,
    UpdateTokenCredentialInput,
)
from src.api.dependencies import CurrentUserDep, ServicesDep, SessionDep, chat_visible
from src.credentials.service import REQUEST_TOOL, audit
from src.db import models as db
from src.errors import AlreadyDecidedError, ConflictError, InvalidRequestError, NotFoundError
from src.observe.credentials import CredentialSecret, refresh_allowed
from src.policy.model import Breadth, Scope

router = router("/credentials", "credentials")

SORT = {
    CredentialsSort.ID: db.Credential.id,
    CredentialsSort.NAME: db.Credential.name,
    CredentialsSort.CREATED_AT: db.Credential.created_at,
    CredentialsSort.UPDATED_AT: db.Credential.updated_at,
}
EVENTS_SORT = {
    CredentialEventsSort.ID: db.CredentialEvent.id,
    CredentialEventsSort.CREATED_AT: db.CredentialEvent.created_at,
}
SECRET_FIELDS = frozenset({"secret_type", "value", "refresh_token", "password"})


async def get_owned_credential(
    credential_id: UUID, principal: CurrentUserDep, session: SessionDep
) -> db.Credential:
    """Return the user's credential; one of a deleted chat does not exist."""
    credential = await session.get(db.Credential, credential_id)
    if (
        credential is None
        or credential.created_by != principal.user_id
        or not await chat_visible(session, credential.chat_id, principal.user_id)
    ):
        raise NotFoundError(f"credential {credential_id} not found")
    return credential


OwnedCredentialDep = Annotated[db.Credential, Depends(get_owned_credential)]


@router.get("")
async def list_credentials(
    principal: CurrentUserDep, session: SessionDep, params: Annotated[ListCredentialsInput, Query()]
) -> ListCredentialsResult:
    """List the user's credentials without their secrets."""
    query = (
        select(db.Credential)
        .outerjoin(db.Chat, db.Chat.id == db.Credential.chat_id)
        .where(
            db.Credential.created_by == principal.user_id,
            or_(db.Credential.chat_id.is_(None), db.Chat.deleted_at.is_(None)),
        )
    )
    if params.chat_id:
        query = query.where(db.Credential.chat_id.in_(params.chat_id))
    if params.is_global is not None:
        query = query.where(
            db.Credential.chat_id.is_(None)
            if params.is_global
            else db.Credential.chat_id.is_not(None)
        )
    if params.status:
        query = query.where(db.Credential.status.in_(params.status))
    if params.secret_type:
        query = query.where(db.Credential.secret_type.in_(params.secret_type))
    rows, info = await paginate(
        session,
        query,
        params,
        sort=SORT[params.sort],
        direction=params.direction,
        key=db.Credential.id,
    )
    return ListCredentialsResult(data=[_out(row) for row in rows], info=info)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_credential(
    body: CreateCredentialArgs,
    principal: CurrentUserDep,
    session: SessionDep,
    services: ServicesDep,
) -> CreateCredentialResult:
    """Store a credential. The secret is encrypted and never returned."""
    data = body.data
    if data.chat_id is not None and not await chat_visible(
        session, data.chat_id, principal.user_id
    ):
        raise NotFoundError(f"chat {data.chat_id} not found")
    credential = db.Credential(
        id=uuid.uuid7(),
        created_by=principal.user_id,
        name=data.name,
        secret_type=data.secret_type,
        hosts=data.hosts,
        grant_agent=data.grants.agent,
        grant_watch=data.grants.watch,
        grant_env=data.grants.env,
        chat_id=data.chat_id,
        expires_at=data.expires_at,
        updated_by=principal.user_id,
    )
    if isinstance(data, CreateTokenCredentialInput):
        credential.header = data.header
        credential.prefix = data.prefix
        credential.refresh_url = data.refresh_url
        secret = CredentialSecret(value=data.value, refresh_token=data.refresh_token)
    else:
        credential.prefix = ""
        credential.username = data.username
        secret = CredentialSecret(value=data.password)
    if not _refresh_valid(credential):
        raise InvalidRequestError("`refresh_url` must be an https URL on one of `hosts`")
    if await _env_conflict(session, credential):
        raise ConflictError(
            f"environment variable {credential.grant_env} is already granted by another credential"
        )
    credential.secret = services.vault.seal(secret)
    session.add(credential)
    await session.flush()
    await audit(session, credential.id, "created", detail=_scope_detail(credential))
    await session.refresh(credential)
    return CreateCredentialResult(data=_out(credential))


@router.post(
    "/requests/{action_id}", status_code=status.HTTP_201_CREATED, responses={503: UNAVAILABLE}
)
async def answer_credential_request(
    action_id: UUID,
    body: AnswerCredentialRequestArgs,
    principal: CurrentUserDep,
    session: SessionDep,
    services: ServicesDep,
) -> AnswerCredentialRequestResult:
    """Enter the credential an agent asked for with `request_credential` and approve the request.

    `secret_type` must be the requested one. The credential gets the requested hosts, the
    `agent` grant and the request's chat; the agent receives only its id. To decline, reject the
    action instead. Repeating the request after it succeeded returns 409.
    """
    action = await session.get(db.Action, action_id, with_for_update=True)
    if (
        action is None
        or action.tool != REQUEST_TOOL
        or not await chat_visible(session, action.chat_id, principal.user_id)
    ):
        raise NotFoundError(f"credential request {action_id} not found")
    if action.status not in db.PENDING_ACTION_STATUSES:
        raise AlreadyDecidedError(f"credential request {action_id} is already decided")
    if await session.get(db.Credential, action_id) is None:
        credential = _requested(action, body.data, principal.user_id)
        if credential.secret_type is not body.data.secret_type:
            raise InvalidRequestError(f"the agent requested a {credential.secret_type.value}")
        if not _refresh_valid(credential):
            raise InvalidRequestError("`refresh_url` must be an https URL on one of `hosts`")
        credential.secret = services.vault.seal(_answer_secret(body.data))
        session.add(credential)
        await session.flush()
        await audit(
            session,
            credential.id,
            "created",
            chat_id=action.chat_id,
            action_id=action.id,
            detail=_scope_detail(credential),
        )
    await session.commit()
    decided, changed = await Actions(services.sessions).decide(
        action_id, db.Decision.APPROVE, Scope.ONCE, Breadth.EXACT
    )
    if not changed and decided.decision is not db.Decision.APPROVE:
        raise AlreadyDecidedError(f"credential request {action_id} is already decided")
    await services.commands.action_decided(decided.chat_id, action_id)
    return AnswerCredentialRequestResult(data=Action.model_validate(decided))


@router.get("/{credential_id}")
async def read_credential(credential: OwnedCredentialDep) -> ReadCredentialResult:
    """Return a credential without its secret."""
    return ReadCredentialResult(data=_out(credential))


@router.patch("/{credential_id}", responses={503: UNAVAILABLE})
async def update_credential(
    credential_id: UUID,
    body: UpdateCredentialArgs,
    credential: OwnedCredentialDep,
    session: SessionDep,
    services: ServicesDep,
) -> UpdateCredentialResult:
    """Change a credential; omitted fields keep their values and later uses follow the change.

    `secret_type` must be the stored one. A new `value` or `password` rotates the secret: the
    `version` grows, `failed_at` clears and sources paused because the credential was rejected
    resume polling. A revoked credential cannot be rotated.
    """
    data = body.data
    if data.secret_type is not credential.secret_type:
        raise InvalidRequestError(f"credential {credential_id} is a {credential.secret_type.value}")
    await session.refresh(credential, with_for_update=True)
    if (
        "chat_id" in data.model_fields_set
        and data.chat_id is not None
        and not await chat_visible(session, data.chat_id, credential.created_by)
    ):
        raise NotFoundError(f"chat {data.chat_id} not found")
    secret = _new_secret(data)
    if secret is not None and credential.status is db.CredentialStatus.REVOKED:
        raise ConflictError(f"credential {credential_id} is revoked; create a new one")
    _apply(credential, data)
    credential.updated_by = credential.created_by
    if not _refresh_valid(credential):
        raise InvalidRequestError("`refresh_url` must be an https URL on one of `hosts`")
    if await _env_conflict(session, credential):
        raise ConflictError(
            f"environment variable {credential.grant_env} is already granted by another credential"
        )
    if data.model_fields_set - SECRET_FIELDS:
        await audit(session, credential.id, "scope_changed", detail=_scope_detail(credential))
    if secret is not None:
        credential.secret = services.vault.seal(secret)
        credential.failed_at = None
        credential.version += 1
        await audit(session, credential.id, "rotated", detail=f"v{credential.version}")
    await session.flush()
    await session.refresh(credential)
    if secret is not None:
        await session.commit()
        for source_id in await services.sources.credential_replaced(credential.id):
            await services.source_schedules.resume(source_id)
    return UpdateCredentialResult(data=_out(credential))


@router.post("/{credential_id}/revoke")
async def revoke_credential(
    credential_id: UUID,
    credential: OwnedCredentialDep,
    principal: CurrentUserDep,
    session: SessionDep,
    services: ServicesDep,
) -> RevokeCredentialResult:
    """Revoke the credential for good and erase its secret; repeating is safe.

    Agents and Observe sources can no longer use it; sources that depend on it pause with an
    `auth_lost` alert at their next poll.
    """
    await session.refresh(credential, with_for_update=True)
    if credential.status is not db.CredentialStatus.REVOKED:
        credential.status = db.CredentialStatus.REVOKED
        credential.secret = services.vault.seal(CredentialSecret(value=""))
        credential.updated_by = principal.user_id
        await audit(session, credential_id, "revoked")
        await session.flush()
        await session.refresh(credential)
    return RevokeCredentialResult(data=_out(credential))


@router.delete("/{credential_id}")
async def delete_credential(
    credential_id: UUID, credential: OwnedCredentialDep, session: SessionDep
) -> DeleteCredentialResult:
    """Delete a credential that no source uses; its audit trail stays."""
    await session.refresh(credential, with_for_update=True)
    if await session.scalar(select(exists().where(db.Source.credential_id == credential_id))):
        raise ConflictError(f"sources use credential {credential_id}")
    deleted = _out(credential)
    await session.delete(credential)
    await audit(session, credential_id, "deleted")
    return DeleteCredentialResult(data=deleted)


@router.get("/{credential_id}/events")
async def list_credential_events(
    credential: OwnedCredentialDep,
    session: SessionDep,
    params: Annotated[ListCredentialEventsInput, Query()],
) -> ListCredentialEventsResult:
    """List the audit trail of the credential."""
    rows, info = await paginate(
        session,
        select(db.CredentialEvent).where(db.CredentialEvent.credential_id == credential.id),
        params,
        sort=EVENTS_SORT[params.sort],
        direction=params.direction,
        key=db.CredentialEvent.id,
    )
    return ListCredentialEventsResult(
        data=[CredentialEvent.model_validate(row) for row in rows], info=info
    )


async def _env_conflict(session: AsyncSession, credential: db.Credential) -> bool:
    """Return whether another active credential grants the same variable in an overlapping scope."""
    if credential.grant_env is None or credential.status is not db.CredentialStatus.ACTIVE:
        return False
    overlap: ColumnElement[bool] = (
        true()
        if credential.chat_id is None
        else or_(db.Credential.chat_id.is_(None), db.Credential.chat_id == credential.chat_id)
    )
    return bool(
        await session.scalar(
            select(
                exists().where(
                    db.Credential.created_by == credential.created_by,
                    db.Credential.id != credential.id,
                    db.Credential.status == db.CredentialStatus.ACTIVE,
                    db.Credential.grant_env == credential.grant_env,
                    overlap,
                )
            )
        )
    )


def _refresh_valid(credential: db.Credential) -> bool:
    return credential.refresh_url is None or refresh_allowed(credential)


def _new_secret(data: UpdateCredentialInput) -> CredentialSecret | None:
    if isinstance(data, UpdateTokenCredentialInput):
        if data.value is None:
            return None
        return CredentialSecret(value=data.value, refresh_token=data.refresh_token)
    return CredentialSecret(value=data.password) if data.password is not None else None


def _apply(credential: db.Credential, data: UpdateCredentialInput) -> None:
    fields = data.model_fields_set
    credential.name = data.name or credential.name
    credential.hosts = data.hosts or credential.hosts
    if data.grants is not None:
        credential.grant_agent = data.grants.agent
        credential.grant_watch = data.grants.watch
        credential.grant_env = data.grants.env
    if "chat_id" in fields:
        credential.chat_id = data.chat_id
    if "expires_at" in fields:
        credential.expires_at = data.expires_at
    if not isinstance(data, UpdateTokenCredentialInput):
        if data.username is not None:
            credential.username = data.username
        return
    if "header" in fields:
        credential.header = data.header
    if data.prefix is not None:
        credential.prefix = data.prefix
    if "refresh_url" in fields:
        credential.refresh_url = data.refresh_url


def _requested(action: db.Action, data: AnswerCredentialRequestInput, owner: UUID) -> db.Credential:
    """Return the credential that answers a `request_credential` action, without its secret."""
    requested = action.pending_arguments
    if not isinstance(requested, dict) or not isinstance(hosts := requested["hosts"], list):
        raise TypeError(f"pending request {action.id} has no arguments")
    credential = db.Credential(
        id=action.id,
        created_by=owner,
        name=data.name or str(requested["name"]),
        secret_type=db.SecretType(str(requested["type"])),
        prefix="",
        hosts=[str(host) for host in hosts],
        grant_agent=True,
        grant_watch=False,
        grant_env=None,
        chat_id=action.chat_id,
        updated_by=owner,
    )
    if isinstance(data, AnswerTokenCredentialRequestInput):
        credential.header = data.header
        credential.prefix = data.prefix
        credential.refresh_url = data.refresh_url
    else:
        credential.username = data.username
    return credential


def _answer_secret(data: AnswerCredentialRequestInput) -> CredentialSecret:
    if isinstance(data, AnswerTokenCredentialRequestInput):
        return CredentialSecret(value=data.value, refresh_token=data.refresh_token)
    return CredentialSecret(value=data.password)


def _out(credential: db.Credential) -> TokenCredential | LoginCredential:
    if credential.secret_type is db.SecretType.TOKEN:
        return TokenCredential.model_validate(credential)
    return LoginCredential.model_validate(credential)


def _scope_detail(credential: db.Credential) -> str:
    grants = [
        *(["agent"] if credential.grant_agent else []),
        *(["watch"] if credential.grant_watch else []),
        *([f"env:{credential.grant_env}"] if credential.grant_env is not None else []),
    ]
    chat = f" chat {credential.chat_id}" if credential.chat_id else ""
    expires = f" until {credential.expires_at.isoformat()}" if credential.expires_at else ""
    return f"{','.join(grants) or '-'} on {','.join(credential.hosts)}{chat}{expires}"
