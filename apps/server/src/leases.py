"""Exclusive leases in PostgreSQL with fencing tokens that grow with every acquisition."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert

from src.db.engine import Sessions
from src.db.models import Lease

USER = "user"
"""Holder of the GUI lease while the operator controls the Computer."""


def gui_key(chat_id: UUID) -> str:
    """Return the key of the chat's GUI lease."""
    return f"gui:{chat_id}"


@dataclass(frozen=True, slots=True)
class Held:
    """The current holder of a lease."""

    holder: str
    token: int
    expires_at: datetime


class Leases:
    """Acquire, renew and release leases."""

    def __init__(self, sessions: Sessions) -> None:
        self._sessions = sessions

    async def acquire(self, key: str, holder: str, ttl: timedelta) -> int | None:
        """Take a free or expired lease; return its new token, or ``None`` if it is held."""
        async with self._sessions.begin() as session:
            return await session.scalar(
                insert(Lease)
                .values(key=key, holder=holder, token=1, expires_at=func.now() + ttl)
                .on_conflict_do_update(
                    index_elements=[Lease.key],
                    set_={
                        "holder": holder,
                        "token": Lease.token + 1,
                        "expires_at": func.now() + ttl,
                    },
                    where=Lease.holder.is_(None) | (Lease.expires_at < func.now()),
                )
                .returning(Lease.token)
            )

    async def renew(self, key: str, holder: str, token: int, ttl: timedelta) -> bool:
        """Extend a lease this holder still has; return whether it did."""
        async with self._sessions.begin() as session:
            renewed = await session.scalar(
                update(Lease)
                .where(
                    Lease.key == key,
                    Lease.holder == holder,
                    Lease.token == token,
                    Lease.expires_at >= func.now(),
                )
                .values(expires_at=func.now() + ttl)
                .returning(Lease.token)
            )
        return renewed is not None

    async def release(self, key: str, holder: str, token: int) -> None:
        """Free the lease if this holder still has it."""
        async with self._sessions.begin() as session:
            await session.execute(
                update(Lease)
                .where(Lease.key == key, Lease.holder == holder, Lease.token == token)
                .values(holder=None, expires_at=None)
            )

    async def current(self, key: str) -> Held | None:
        """Return the unexpired holder of the lease."""
        async with self._sessions() as session:
            row = await session.scalar(
                select(Lease).where(
                    Lease.key == key, Lease.holder.is_not(None), Lease.expires_at >= func.now()
                )
            )
        if row is None or row.holder is None or row.expires_at is None:
            return None
        return Held(row.holder, row.token, row.expires_at)
