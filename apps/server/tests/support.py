"""Users for tests: every owned row needs one."""

import uuid
from uuid import UUID

from src.db.engine import Sessions
from src.db.models import User

_PASSWORD_HASH = "!"
"""No password signs in as a user created here; tests authenticate through the API."""


async def add_user(sessions: Sessions, name: str = "tester") -> UUID:
    """Insert a user and return its id."""
    user = User(
        id=uuid.uuid7(),
        username=f"{name}-{uuid.uuid7().hex[-12:]}",
        name=name,
        password_hash=_PASSWORD_HASH,
    )
    async with sessions.begin() as session:
        session.add(user)
    return user.id
