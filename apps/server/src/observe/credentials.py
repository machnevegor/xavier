"""Credential secrets encrypted with Fernet, the headers they add, and their use by Watch."""

import json
from urllib.parse import urlencode, urlsplit
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken
from pydantic import BaseModel, ValidationError
from sqlalchemy import func, or_, select, update

from src.credentials.hosts import host_allowed
from src.db.engine import Sessions
from src.db.models import (
    Credential,
    CredentialEvent,
    CredentialGrant,
    CredentialStatus,
    SecretType,
)
from src.observe.fetch import FetchError, HttpFetcher

REFRESH_TIMEOUT = 30.0
DEFAULT_HEADER = "Authorization"
_REFRESH_MAX_BYTES = 64 * 1024


class CredentialSecret(BaseModel):
    """The secret part of a credential."""

    value: str
    """Token value, or the password of a login."""
    refresh_token: str | None = None
    """Renews a token at the credential's ``refresh_url``."""


class Vault:
    """Encrypts secrets at rest with ``observe.credentials_key``."""

    def __init__(self, key: str) -> None:
        self._fernet = Fernet(key)

    def encrypt(self, plaintext: str) -> str:
        """Return the Fernet token of ``plaintext``."""
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, token: str) -> str:
        """Return the plaintext of a Fernet token.

        Raises
        ------
        ValueError
            If the token was not made with this key.
        """
        try:
            return self._fernet.decrypt(token.encode()).decode()
        except InvalidToken as error:
            raise ValueError(
                "the secret cannot be decrypted with observe.credentials_key"
            ) from error

    def seal(self, secret: CredentialSecret) -> str:
        """Return the stored form of a credential secret."""
        return self.encrypt(secret.model_dump_json())

    def open(self, token: str) -> CredentialSecret:
        """Return a credential secret from its stored form."""
        return CredentialSecret.model_validate_json(self.decrypt(token))


def request_headers(credential: Credential, secret: CredentialSecret) -> dict[str, str]:
    """Return the header that carries a token: ``header: prefix + value``.

    Raises
    ------
    ValueError
        If the credential is a login, which is typed into forms and never sent in headers.
    """
    if credential.secret_type is not SecretType.TOKEN:
        raise ValueError("a login is typed into login forms, not sent in headers")
    return {credential.header or DEFAULT_HEADER: f"{credential.prefix}{secret.value}"}


def url_host(url: str) -> str:
    """Return the lowercase host of ``url``, or ``""``."""
    return (urlsplit(url).hostname or "").lower().rstrip(".")


def refresh_allowed(credential: Credential) -> bool:
    """Return whether the token endpoint is HTTPS on one of the credential's hosts."""
    url = credential.refresh_url
    return (
        url is not None
        and urlsplit(url).scheme == "https"
        and host_allowed(url_host(url), list(credential.hosts))
    )


class Credentials:
    """Credential use by Watch polls: headers on the credential hosts, refresh and rejection."""

    def __init__(self, sessions: Sessions, vault: Vault, fetcher: HttpFetcher) -> None:
        self._sessions = sessions
        self._vault = vault
        self._fetcher = fetcher

    async def headers(
        self, credential_id: UUID, *, owner: UUID, chat_id: UUID | None, url: str
    ) -> dict[str, str] | None:
        """Return the headers of a request to ``url``; ``None`` if Watch may not send them there.

        The credential must be the owner's active token with the ``watch`` grant, global or of
        the source's chat ``chat_id``, and the host of ``url`` must match its hosts.
        """
        credential = await self._watchable(credential_id, owner, chat_id)
        if credential is None or not host_allowed(url_host(url), list(credential.hosts)):
            return None
        return request_headers(credential, self._vault.open(credential.secret))

    async def refresh(self, credential_id: UUID, *, owner: UUID, chat_id: UUID | None) -> bool:
        """Renew a token through its token endpoint; ``False`` when it cannot be renewed.

        Only a token with ``refresh_url`` and a refresh token is renewed. The endpoint must be
        HTTPS on one of the credential's hosts; it gets only an OAuth 2.0 ``refresh_token`` grant,
        redirects are not followed, and it must answer with JSON that has ``access_token`` and
        optionally a new ``refresh_token``. A renewal is recorded in the audit trail.
        """
        credential = await self._watchable(credential_id, owner, chat_id)
        if credential is None or not refresh_allowed(credential):
            return False
        secret, url = self._vault.open(credential.secret), credential.refresh_url
        if secret.refresh_token is None or url is None:
            return False
        form = urlencode({"grant_type": "refresh_token", "refresh_token": secret.refresh_token})
        try:
            result = await self._fetcher.fetch(
                url,
                method="POST",
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                },
                content=form.encode(),
                max_bytes=_REFRESH_MAX_BYTES,
                within=REFRESH_TIMEOUT,
                follow_redirects=False,
            )
            if result.status != 200 or result.truncated:  # noqa: PLR2004
                return False
            renewed = _Token.model_validate(json.loads(result.body))
        except FetchError, ValueError, ValidationError:
            return False
        sealed = self._vault.seal(
            CredentialSecret(
                value=renewed.access_token,
                refresh_token=renewed.refresh_token or secret.refresh_token,
            )
        )
        async with self._sessions.begin() as session:
            await session.execute(
                update(Credential).where(Credential.id == credential_id).values(secret=sealed)
            )
            session.add(
                CredentialEvent(
                    credential_id=credential_id,
                    kind="refreshed",
                    chat_id=chat_id,
                    detail=f"{CredentialGrant.WATCH.value} refresh at {url_host(url)}",
                )
            )
        return True

    async def reject(self, credential_id: UUID) -> None:
        """Record that the credential was rejected."""
        async with self._sessions.begin() as session:
            await session.execute(
                update(Credential)
                .where(Credential.id == credential_id)
                .values(failed_at=func.coalesce(Credential.failed_at, func.now()))
            )

    async def _watchable(
        self, credential_id: UUID, owner: UUID, chat_id: UUID | None
    ) -> Credential | None:
        async with self._sessions() as session:
            return await session.scalar(
                select(Credential).where(
                    Credential.id == credential_id,
                    Credential.created_by == owner,
                    or_(Credential.chat_id.is_(None), Credential.chat_id == chat_id),
                    Credential.status == CredentialStatus.ACTIVE,
                    Credential.grant_watch.is_(True),
                    Credential.secret_type == SecretType.TOKEN,
                    or_(Credential.expires_at.is_(None), Credential.expires_at > func.now()),
                )
            )


class _Token(BaseModel):
    access_token: str
    refresh_token: str | None = None
