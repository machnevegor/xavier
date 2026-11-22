"""Password changes record the acting user and leave a command-line change unattributed."""

from typing import cast
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.dml import Update

from src.auth.service import MIN_PASSWORD, Accounts, check_password
from src.config import AuthSettings
from src.db.models import Locale
from src.errors import InvalidRequestError

PASSWORD = "long-enough-pw"


class _Session:
    def __init__(self) -> None:
        self.statements: list[Update] = []

    async def execute(self, statement: Update) -> None:
        self.statements.append(statement)


def _params(statement: Update) -> dict[str, object]:
    return statement.compile(dialect=postgresql.dialect()).params


async def test_set_locale_records_the_actor() -> None:
    session = _Session()
    accounts = Accounts(AuthSettings(), cast(AsyncSession, session))
    actor = uuid4()

    await accounts.set_locale(actor, Locale.RU, by=actor)

    params = _params(session.statements[0])
    assert params["locale"] == Locale.RU
    assert params["updated_by"] == actor


async def test_set_password_records_the_actor_and_a_cli_change_does_not() -> None:
    session = _Session()
    accounts = Accounts(AuthSettings(), cast(AsyncSession, session))
    actor = uuid4()

    await accounts.set_password(uuid4(), PASSWORD, by=actor)
    await accounts.set_password(uuid4(), PASSWORD)

    assert _params(session.statements[0])["updated_by"] == actor
    assert "updated_by" not in _params(session.statements[2])


async def test_set_password_keeps_the_selected_session() -> None:
    session = _Session()
    accounts = Accounts(AuthSettings(), cast(AsyncSession, session))
    current = uuid4()

    await accounts.set_password(uuid4(), PASSWORD, keep=current)

    statement = session.statements[1]
    assert "sessions.id !=" in str(statement)
    assert current in _params(statement).values()


def test_password_policy_rejects_a_short_password() -> None:
    with pytest.raises(InvalidRequestError):
        check_password("x" * (MIN_PASSWORD - 1))

    assert check_password("x" * MIN_PASSWORD) == "x" * MIN_PASSWORD
