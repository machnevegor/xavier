"""Users, sessions and API keys: passwords are Argon2id hashes, tokens are stored as SHA-256."""

import hashlib
import hmac
import re
import secrets
import string
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import ColumnElement, func, select, true, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import AuthSettings
from src.db.models import ApiKey, AuthSession, Locale, User
from src.errors import ConflictError, InvalidRequestError, NotFoundError

API_KEY_PREFIX = "xv_"
USERNAME = re.compile(r"^[a-z0-9][a-z0-9._-]{1,31}$")
"""Lowercase sign-in names of 2 to 32 characters."""
MIN_PASSWORD = 12
MAX_PASSWORD = 1024
_ALPHABET = string.ascii_letters + string.digits
_KEY_ID_LENGTH = 8
_KEY_SECRET_LENGTH = 40
_SEEN_INTERVAL = timedelta(minutes=1)
"""Requests within this time of the last recorded one do not update ``last_seen_at``."""
_hasher = PasswordHasher()
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))
"""Verified for unknown usernames so that a sign-in takes as long as for a known one."""


@dataclass(frozen=True, slots=True)
class Principal:
    """Who makes a request: a user through a browser session or through an API key."""

    user_id: UUID
    session_id: UUID | None = None
    api_key_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class NewSession:
    """A created session and its cookie value, which is not stored."""

    session: AuthSession
    token: str


@dataclass(frozen=True, slots=True)
class NewApiKey:
    """A created API key and its full value, which is shown once and not stored."""

    key: ApiKey
    value: str


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _random(length: int) -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(length))


def check_password(password: str) -> str:
    """Return ``password`` or raise `InvalidRequestError` if it is too short or too long."""
    if not MIN_PASSWORD <= len(password) <= MAX_PASSWORD:
        raise InvalidRequestError(
            f"the password must have {MIN_PASSWORD} to {MAX_PASSWORD} characters"
        )
    return password


def normalize_username(username: str) -> str:
    """Return the stored form of ``username`` or raise `InvalidRequestError`."""
    normalized = username.strip().lower()
    if not USERNAME.fullmatch(normalized):
        raise InvalidRequestError(
            "the username must have 2 to 32 lowercase letters, digits, dots, dashes or "
            "underscores and start with a letter or digit"
        )
    return normalized


def live_sessions(settings: AuthSettings, now: datetime) -> ColumnElement[bool]:
    """Return the condition of sessions that can still be used at ``now``."""
    return (
        AuthSession.revoked_at.is_(None)
        & (AuthSession.expires_at > now)
        & (AuthSession.last_seen_at > now - settings.session_idle_ttl)
    )


class Accounts:
    """Users, their sessions and API keys in one database session; the caller commits."""

    def __init__(self, settings: AuthSettings, session: AsyncSession) -> None:
        self._settings = settings
        self._session = session

    async def create_user(self, username: str, password: str, *, name: str = "") -> User:
        """Create a user; raise `ConflictError` if the username is taken."""
        username = normalize_username(username)
        password_hash = _hasher.hash(check_password(password))
        taken = await self._session.scalar(
            select(User.id).where(User.username == username, User.deleted_at.is_(None))
        )
        if taken is not None:
            raise ConflictError(f"user {username!r} already exists")
        user = User(
            id=uuid.uuid7(),
            username=username,
            name=name.strip() or username,
            password_hash=password_hash,
        )
        self._session.add(user)
        await self._session.flush()
        await self._session.refresh(user)
        return user

    async def users(self) -> Sequence[User]:
        """Return the users that are not deleted, oldest first."""
        return (
            await self._session.scalars(
                select(User).where(User.deleted_at.is_(None)).order_by(User.id)
            )
        ).all()

    async def user(self, user_id: UUID) -> User:
        """Return the user or raise `NotFoundError`."""
        user = await self._session.get(User, user_id)
        if user is None or user.deleted_at is not None:
            raise NotFoundError(f"user {user_id} not found")
        return user

    async def find_user(self, username: str) -> User:
        """Return the user named ``username`` or raise `NotFoundError`."""
        user = await self._session.scalar(
            select(User).where(User.username == username.strip().lower(), User.deleted_at.is_(None))
        )
        if user is None:
            raise NotFoundError(f"user {username!r} not found")
        return user

    async def set_locale(self, user_id: UUID, locale: Locale, *, by: UUID) -> None:
        """Set the interface language and record ``by`` as the actor."""
        await self._session.execute(
            update(User).where(User.id == user_id).values(locale=locale, updated_by=by)
        )

    async def set_password(
        self, user_id: UUID, password: str, *, by: UUID | None = None, keep: UUID | None = None
    ) -> None:
        """Replace the password and sign out every session except ``keep``.

        ``by`` is the user who changed it. A command-line change has none and leaves
        ``updated_by`` as it was.
        """
        hashed = _hasher.hash(check_password(password))
        statement = update(User).where(User.id == user_id)
        if by is None:
            await self._session.execute(statement.values(password_hash=hashed))
        else:
            await self._session.execute(statement.values(password_hash=hashed, updated_by=by))
        await self._session.execute(
            update(AuthSession)
            .where(
                AuthSession.user_id == user_id,
                AuthSession.revoked_at.is_(None),
                AuthSession.id != keep if keep is not None else true(),
            )
            .values(revoked_at=func.now())
        )

    async def delete_user(self, user_id: UUID, *, by: UUID | None = None) -> None:
        """Mark the user deleted and revoke their sessions and API keys."""
        deleted = await self._session.scalar(
            update(User)
            .where(User.id == user_id, User.deleted_at.is_(None))
            .values(deleted_at=func.now(), deleted_by=by)
            .returning(User.id)
        )
        if deleted is None:
            raise NotFoundError(f"user {user_id} not found")
        await self._session.execute(
            update(AuthSession)
            .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
            .values(revoked_at=func.now())
        )
        await self._session.execute(
            update(ApiKey)
            .where(ApiKey.user_id == user_id, ApiKey.revoked_at.is_(None))
            .values(revoked_at=func.now())
        )

    async def verify(self, username: str, password: str) -> User | None:
        """Return the user if the password is theirs; take as long for unknown usernames."""
        user = await self._session.scalar(
            select(User).where(User.username == username.strip().lower(), User.deleted_at.is_(None))
        )
        try:
            _hasher.verify(user.password_hash if user else _DUMMY_HASH, password)
        except VerificationError, InvalidHashError:
            return None
        if user is None:
            return None
        if _hasher.check_needs_rehash(user.password_hash):
            user.password_hash = _hasher.hash(password)
        return user

    async def start_session(self, user_id: UUID, *, user_agent: str, ip: str | None) -> NewSession:
        """Create a session of the user and return it with its cookie value."""
        token = secrets.token_urlsafe(32)
        created = AuthSession(
            id=uuid.uuid7(),
            user_id=user_id,
            token_hash=_digest(token),
            user_agent=user_agent[:512],
            ip=ip,
            expires_at=datetime.now(UTC) + self._settings.session_ttl,
        )
        self._session.add(created)
        await self._session.flush()
        await self._session.refresh(created)
        return NewSession(created, token)

    async def resolve(self, credential: str) -> Principal | None:
        """Return who a session cookie value or an API key belongs to, if it is valid."""
        if credential.startswith(API_KEY_PREFIX):
            return await self._resolve_key(credential)
        return await self._resolve_session(credential)

    async def _resolve_session(self, token: str) -> Principal | None:
        now = datetime.now(UTC)
        row = await self._session.scalar(
            select(AuthSession)
            .join(User, User.id == AuthSession.user_id)
            .where(
                AuthSession.token_hash == _digest(token),
                live_sessions(self._settings, now),
                User.deleted_at.is_(None),
            )
        )
        if row is None:
            return None
        if now - row.last_seen_at > _SEEN_INTERVAL:
            row.last_seen_at = now
        return Principal(row.user_id, session_id=row.id)

    async def _resolve_key(self, value: str) -> Principal | None:
        prefix = value[: len(API_KEY_PREFIX) + _KEY_ID_LENGTH]
        now = datetime.now(UTC)
        key = await self._session.scalar(
            select(ApiKey)
            .join(User, User.id == ApiKey.user_id)
            .where(ApiKey.prefix == prefix, ApiKey.revoked_at.is_(None), User.deleted_at.is_(None))
        )
        if key is None or not hmac.compare_digest(key.key_hash, _digest(value)):
            return None
        if key.last_used_at is None or now - key.last_used_at > _SEEN_INTERVAL:
            key.last_used_at = now
        return Principal(key.user_id, api_key_id=key.id)

    async def active(self, principal: Principal) -> bool:
        """Return whether the principal's session or key is still valid; for long streams."""
        now = datetime.now(UTC)
        if principal.session_id is not None:
            query = (
                select(AuthSession.id)
                .join(User, User.id == AuthSession.user_id)
                .where(
                    AuthSession.id == principal.session_id,
                    live_sessions(self._settings, now),
                    User.deleted_at.is_(None),
                )
            )
        else:
            query = (
                select(ApiKey.id)
                .join(User, User.id == ApiKey.user_id)
                .where(
                    ApiKey.id == principal.api_key_id,
                    ApiKey.revoked_at.is_(None),
                    User.deleted_at.is_(None),
                )
            )
        return await self._session.scalar(query) is not None

    async def revoke_session(self, user_id: UUID, session_id: UUID) -> AuthSession:
        """Sign out one of the user's sessions; return it."""
        revoked = await self._session.scalar(
            update(AuthSession)
            .where(AuthSession.id == session_id, AuthSession.user_id == user_id)
            .values(revoked_at=func.coalesce(AuthSession.revoked_at, func.now()))
            .returning(AuthSession)
        )
        if revoked is None:
            raise NotFoundError(f"session {session_id} not found")
        return revoked

    async def create_key(self, user_id: UUID, name: str) -> NewApiKey:
        """Create an API key and return it with its full value."""
        name = name.strip()
        if not name:
            raise InvalidRequestError("the API key needs a name")
        prefix = f"{API_KEY_PREFIX}{_random(_KEY_ID_LENGTH)}"
        value = f"{prefix}_{_random(_KEY_SECRET_LENGTH)}"
        key = ApiKey(
            id=uuid.uuid7(), user_id=user_id, name=name, prefix=prefix, key_hash=_digest(value)
        )
        self._session.add(key)
        await self._session.flush()
        await self._session.refresh(key)
        return NewApiKey(key, value)

    async def revoke_key(self, user_id: UUID, key_id: UUID) -> ApiKey:
        """Revoke one of the user's API keys; return it."""
        revoked = await self._session.scalar(
            update(ApiKey)
            .where(ApiKey.id == key_id, ApiKey.user_id == user_id)
            .values(revoked_at=func.coalesce(ApiKey.revoked_at, func.now()))
            .returning(ApiKey)
        )
        if revoked is None:
            raise NotFoundError(f"API key {key_id} not found")
        return revoked
