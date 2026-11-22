"""Use of credentials on the agent's behalf, with an audit trail.

The agent names a credential by id. The backend checks the owner, the chat, the status, the
expiry and the grant, sends the value itself and records the use. With ``grant_agent`` a token
goes only into ``web_fetch`` headers and a login only into login forms of the Computer's
browser, both only on the credential hosts. With ``grant_env`` the value becomes an environment
variable of the chat's ``code_run`` executions, where the executed code may read it and send it
anywhere: enabling the grant is that authorization. Values never reach the model.
"""

import re
from datetime import UTC, datetime
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import ColumnElement, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.credentials.hosts import host_allowed, host_pattern
from src.db.engine import Sessions
from src.db.models import (
    Credential,
    CredentialEvent,
    CredentialGrant,
    CredentialStatus,
    Execution,
    SecretType,
)
from src.observe.credentials import CredentialSecret, Vault, request_headers
from src.policy.redact import Redactor

__all__ = [
    "REQUEST_TOOL",
    "AgentCredentials",
    "CredentialDeniedError",
    "audit",
    "env_name",
    "host_allowed",
    "host_pattern",
    "usable",
]

REQUEST_TOOL = "request_credential"
"""The credential that answers a request has the id of the request's action."""
ENV_NAME = re.compile(r"^[A-Z_][A-Z0-9_]{0,63}$")
_RESERVED_ENV = frozenset(
    {"PATH", "HOME", "USER", "SHELL", "PWD", "TMPDIR", "NODE_OPTIONS", "BASH_ENV", "ENV", "IFS"}
)
_RESERVED_ENV_PREFIXES = ("LD_", "PYTHON", "XAVIER_", "BASH_FUNC_")


class CredentialDeniedError(Exception):
    """The credential may not be used for this request."""


def env_name(value: str) -> str:
    """Return ``value`` if it may name a credential's environment variable.

    Raises
    ------
    ValueError
        If it is not ``[A-Z_][A-Z0-9_]*`` of at most 64 characters, or the runtime uses it.
    """
    if not ENV_NAME.fullmatch(value):
        raise ValueError(
            "the variable name must be uppercase letters, digits and underscores, "
            "start with a letter or underscore and have at most 64 characters"
        )
    if value in _RESERVED_ENV or value.startswith(_RESERVED_ENV_PREFIXES):
        raise ValueError(f"{value} is reserved by the runtime")
    return value


async def audit(  # noqa: PLR0913 - the fields of one audit record
    session: AsyncSession,
    credential_id: UUID,
    kind: str,
    *,
    chat_id: UUID | None = None,
    action_id: UUID | None = None,
    detail: str = "",
) -> None:
    """Add an audit record of the credential."""
    session.add(
        CredentialEvent(
            credential_id=credential_id,
            kind=kind,
            chat_id=chat_id,
            action_id=action_id,
            detail=detail,
        )
    )


def usable(credential: Credential, now: datetime) -> str | None:
    """Return why the credential cannot be used at all, or ``None``."""
    if credential.status is not CredentialStatus.ACTIVE:
        return f"credential {credential.id} is revoked"
    if credential.expires_at is not None and credential.expires_at <= now:
        return f"credential {credential.id} expired"
    return None


def _denial(
    credential: Credential, chat_id: UUID, host: str | None, secret_type: SecretType
) -> str | None:
    """Return why the agent may not use the credential here, or ``None``.

    ``host`` ``None`` leaves the host check to the point of use, which gets the patterns.
    """
    if reason := usable(credential, datetime.now(UTC)):
        return reason
    if not credential.grant_agent:
        return f"credential {credential.id} is not granted to the agent"
    if credential.secret_type is not secret_type:
        return f"credential {credential.id} is a {credential.secret_type.value}"
    if credential.chat_id not in {None, chat_id}:
        return f"credential {credential.id} belongs to another chat"
    if host is not None and not host_allowed(host, list(credential.hosts)):
        return f"credential {credential.id} may not be sent to {host}"
    return None


def _scope(owner: UUID, chat_id: UUID) -> list[ColumnElement[bool]]:
    """Return the conditions of credentials a chat of ``owner`` may use at all."""
    return [
        Credential.created_by == owner,
        Credential.status == CredentialStatus.ACTIVE,
        or_(Credential.chat_id.is_(None), Credential.chat_id == chat_id),
        or_(Credential.expires_at.is_(None), Credential.expires_at > func.now()),
    ]


class AgentCredentials:
    """Credentials as tools and ``code_run`` use them."""

    def __init__(self, sessions: Sessions, vault: Vault, redactor: Redactor) -> None:
        self._sessions = sessions
        self._vault = vault
        self._redactor = redactor

    async def available(self, chat_id: UUID, owner: UUID) -> list[dict[str, JsonValue]]:
        """Return what the chat's agent may use: ids, names, hosts and variable names, no values."""
        async with self._sessions() as session:
            rows = await session.scalars(
                select(Credential)
                .where(
                    *_scope(owner, chat_id),
                    or_(Credential.grant_agent.is_(True), Credential.grant_env.is_not(None)),
                )
                .order_by(Credential.id)
            )
            return [_listing(row) for row in rows]

    async def headers(
        self,
        credential_id: UUID,
        *,
        chat_id: UUID,
        owner: UUID,
        action_id: UUID | None,
        host: str,
    ) -> dict[str, str]:
        """Return the request header of a token for a request to ``host``.

        Raises
        ------
        CredentialDeniedError
            If the agent may not send it there.
        """
        credential, secret = await self._take(
            credential_id,
            chat_id=chat_id,
            owner=owner,
            action_id=action_id,
            host=host,
            secret_type=SecretType.TOKEN,
        )
        return request_headers(credential, secret)

    async def for_browser(
        self, credential_id: UUID, *, chat_id: UUID, owner: UUID, action_id: UUID
    ) -> tuple[str, list[str]]:
        """Return the password of a login and the host patterns of pages it may be typed into.

        Raises
        ------
        CredentialDeniedError
            If the agent may not use it.
        """
        credential, secret = await self._take(
            credential_id,
            chat_id=chat_id,
            owner=owner,
            action_id=action_id,
            host=None,
            secret_type=SecretType.LOGIN,
        )
        return secret.value, list(credential.hosts)

    async def environment(self, execution: Execution) -> dict[str, str]:
        """Return the variables the credentials with ``grant_env`` give a chat's ``code_run``.

        A credential of the chat wins over a global one with the same name. Every variable set
        is recorded as a use; the values are also redacted from logs and tool results.
        """
        chat_id = execution.chat_id
        if chat_id is None:
            return {}
        async with self._sessions.begin() as session:
            rows = list(
                (
                    await session.scalars(
                        select(Credential)
                        .where(
                            *_scope(execution.created_by, chat_id),
                            Credential.grant_env.is_not(None),
                        )
                        .order_by(Credential.chat_id.is_(None), Credential.id)
                    )
                ).all()
            )
            chosen: dict[str, Credential] = {}
            for row in rows:
                if row.grant_env is not None:
                    chosen.setdefault(row.grant_env, row)
            now = datetime.now(UTC)
            for name, credential in chosen.items():
                credential.last_used_at = now
                await audit(
                    session,
                    credential.id,
                    "used",
                    chat_id=chat_id,
                    action_id=execution.action_id,
                    detail=f"{CredentialGrant.ENV.value} {name} v{credential.version}",
                )
        env: dict[str, str] = {}
        for name, credential in chosen.items():
            secret = self._vault.open(credential.secret)
            env[name] = secret.value
            self._redactor.add(secret.value)
        return env

    async def rejected(self, credential_id: UUID, *, chat_id: UUID | None, detail: str) -> None:
        """Record that a service rejected the credential."""
        async with self._sessions.begin() as session:
            await session.execute(
                update(Credential)
                .where(Credential.id == credential_id)
                .values(failed_at=func.coalesce(Credential.failed_at, func.now()))
            )
            await audit(session, credential_id, "rejected", chat_id=chat_id, detail=detail)

    async def _take(  # noqa: PLR0913 - the scope of one use
        self,
        credential_id: UUID,
        *,
        chat_id: UUID,
        owner: UUID,
        action_id: UUID | None,
        host: str | None,
        secret_type: SecretType,
    ) -> tuple[Credential, CredentialSecret]:
        async with self._sessions.begin() as session:
            credential = await session.get(Credential, credential_id)
            if credential is None or credential.created_by != owner:
                raise CredentialDeniedError(f"credential {credential_id} not found")
            reason = _denial(credential, chat_id, host, secret_type)
            target = host if host is not None else ",".join(credential.hosts)
            grant = CredentialGrant.AGENT.value
            if reason is not None:
                await audit(
                    session,
                    credential_id,
                    "denied",
                    chat_id=chat_id,
                    action_id=action_id,
                    detail=f"{grant} {target}: {reason}",
                )
            else:
                credential.last_used_at = datetime.now(UTC)
                await audit(
                    session,
                    credential_id,
                    "used",
                    chat_id=chat_id,
                    action_id=action_id,
                    detail=f"{grant} {target} v{credential.version}",
                )
        if reason is not None:
            raise CredentialDeniedError(reason)
        secret = self._vault.open(credential.secret)
        self._redactor.add(secret.value, *([secret.refresh_token] if secret.refresh_token else []))
        return credential, secret


def _listing(credential: Credential) -> dict[str, JsonValue]:
    """Return what the agent learns of a credential: never its value."""
    listing: dict[str, JsonValue] = {
        "credential_id": str(credential.id),
        "name": credential.name,
        "type": credential.secret_type.value,
        "hosts": list(credential.hosts),
        "agent": credential.grant_agent,
    }
    if credential.username is not None:
        listing["username"] = credential.username
    if credential.grant_env is not None:
        listing["env"] = credential.grant_env
    return listing
