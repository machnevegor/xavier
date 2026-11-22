import asyncio
import uuid
from collections.abc import AsyncIterator

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Connection

from src.agent.gateway import Actions, StaleAttemptError
from src.config import Settings
from src.db.engine import Sessions, create_engine, create_sessions, migrate
from src.db.models import (
    DECISION_STATUS,
    Action,
    ActionStatus,
    Base,
    Chat,
    ChatStatus,
    Decision,
    Turn,
    TurnStatus,
)
from src.events import NewEvent, append_events, read_events
from src.policy.model import Effect, Mode, Risk
from tests.support import add_user

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


@pytest.fixture
async def sessions(settings: Settings) -> AsyncIterator[Sessions]:
    await asyncio.to_thread(migrate, settings.database)
    engine = create_engine(settings.database)
    try:
        yield create_sessions(engine)
    finally:
        await engine.dispose()


async def _turn(sessions: Sessions, attempt: int = 1) -> Turn:
    chat = Chat(
        id=uuid.uuid7(),
        created_by=await add_user(sessions),
        title="t",
        status=ChatStatus.READY,
        model="test",
        reasoning="off",
        mode=Mode.SUPERVISED,
        tool_groups=["observe"],
        execute_network=False,
        budget_usd=1.0,
        max_steps=50,
    )
    turn = Turn(id=uuid.uuid7(), chat_id=chat.id, attempt=attempt, status=TurnStatus.RUNNING)
    async with sessions.begin() as session:
        session.add(chat)
        await session.flush()
        session.add(turn)
    return turn


def _action(turn: Turn, attempt: int = 1) -> Action:
    return Action(
        id=uuid.uuid7(),
        chat_id=turn.chat_id,
        turn_id=turn.id,
        attempt=attempt,
        tool="send",
        arguments={},
        pending_arguments={"to": "bob"},
        args_hash="h",
        resources=[],
        risk=Risk.WRITE,
        effect=Effect.ASK,
        status=ActionStatus.AWAITING_APPROVAL,
    )


def _differences(connection: Connection) -> list[object]:
    return compare_metadata(MigrationContext.configure(connection), Base.metadata)


async def test_migrations_match_the_models(sessions: Sessions) -> None:
    async with sessions() as session:
        connection = await session.connection()
        assert await connection.run_sync(_differences) == []


async def test_concurrent_decisions_have_exactly_one_winner(sessions: Sessions) -> None:
    actions = Actions(sessions)
    action = _action(await _turn(sessions))
    await actions.create(action, {})

    results = await asyncio.gather(
        *(
            actions.transition(
                action.id,
                {ActionStatus.AWAITING_APPROVAL, ActionStatus.SUSPENDED},
                DECISION_STATUS[decision],
                decision=decision,
            )
            for decision in (Decision.APPROVE, Decision.REJECT, Decision.CANCEL) * 3
        )
    )

    stored = await actions.get(action.id)
    assert results.count(True) == 1
    assert stored.decision is not None
    assert stored.status is DECISION_STATUS[stored.decision]


async def test_final_status_clears_pending_arguments(sessions: Sessions) -> None:
    actions = Actions(sessions)
    action = _action(await _turn(sessions))
    await actions.create(action, {})

    await actions.transition(action.id, {ActionStatus.AWAITING_APPROVAL}, ActionStatus.REJECTED)

    stored = await actions.get(action.id)
    assert stored.pending_arguments is None
    assert stored.finished_at is not None


async def test_action_of_a_stale_attempt_is_not_stored(sessions: Sessions) -> None:
    actions = Actions(sessions)
    turn = await _turn(sessions, attempt=2)

    with pytest.raises(StaleAttemptError):
        await actions.create(_action(turn, attempt=1), {})
    await actions.create(_action(turn, attempt=2), {})


async def test_events_are_read_in_order_after_a_cursor(sessions: Sessions) -> None:
    turn = await _turn(sessions)
    for index in range(3):
        async with sessions.begin() as session:
            await append_events(session, turn.chat_id, [NewEvent("e", {"i": index})])

    async with sessions() as session:
        first = await read_events(session, turn.chat_id, None, 2)
        rest = await read_events(session, turn.chat_id, first[-1].id, 10)

    assert [event.data for event in [*first, *rest]] == [{"i": 0}, {"i": 1}, {"i": 2}]
