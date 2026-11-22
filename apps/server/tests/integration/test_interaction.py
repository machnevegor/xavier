"""Turns of a real Harness runtime against the scripted model of `fake_llm`."""

import asyncio
import contextlib
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from pydantic import JsonValue, SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from types_aiobotocore_s3.client import S3Client

from src.agent.gateway import Actions, Executor, Gateway
from src.agent.questions import Questions
from src.agent.runner import Runner
from src.config import Settings
from src.db.engine import Sessions, create_engine, create_sessions, migrate
from src.db.models import (
    Chat,
    ChatStatus,
    Event,
    Message,
    MessageStatus,
    TitleSource,
    Turn,
    TurnStatus,
)
from src.events import (
    ACTIONS_CHANNEL,
    MESSAGES_CHANNEL,
    QUESTIONS_CHANNEL,
    Listener,
    notify,
)
from src.orchestration.models import TurnInput, TurnOutcome
from src.orchestration.worker import serve
from src.policy.model import Mode
from src.policy.redact import REDACTED, Redactor
from src.storage.artifacts import Artifacts
from src.storage.blobs import S3Adapter
from src.tools.registry import Registry
from tests.fakes import services
from tests.integration.fake_llm import FakeLLM, Reply
from tests.support import add_user

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


@dataclass
class Stand:
    runner: Runner
    sessions: Sessions
    llm: FakeLLM

    async def turn(self, text: str) -> TurnInput:
        """Store a working chat with ``text`` queued and return its next turn."""
        chat = Chat(
            id=uuid.uuid7(),
            created_by=await add_user(self.sessions),
            title="interaction",
            title_source=TitleSource.FALLBACK,
            status=ChatStatus.WORKING,
            model="test",
            reasoning="off",
            mode=Mode.SUPERVISED,
            tool_groups=["observe"],
            execute_network=False,
            budget_usd=1.0,
            max_steps=50,
        )
        turn = Turn(id=uuid.uuid7(), chat_id=chat.id, status=TurnStatus.RUNNING)
        async with self.sessions.begin() as session:
            session.add(chat)
            await session.flush()
            session.add(turn)
            await self.queue(session, chat.id, text)
        return TurnInput(chat_id=chat.id, turn_id=turn.id)

    @staticmethod
    async def queue(session: AsyncSession, chat_id: uuid.UUID, text: str) -> None:
        """Queue operator input the way the API does, waking a running turn."""
        session.add(
            Message(
                id=uuid.uuid7(),
                chat_id=chat_id,
                text=text,
                image_artifact_ids=[],
                file_artifact_ids=[],
                status=MessageStatus.QUEUED,
            )
        )
        await notify(session, MESSAGES_CHANNEL, str(chat_id))

    async def run(self, turn: TurnInput) -> TurnOutcome:
        return await self.runner.run(turn, 1, lambda: None)

    async def assistant(self, turn: TurnInput) -> list[dict[str, JsonValue]]:
        async with self.sessions() as session:
            rows = await session.scalars(
                select(Event)
                .where(Event.turn_id == turn.turn_id, Event.kind == "assistant")
                .order_by(Event.id)
            )
            return [row.data for row in rows if isinstance(row.data, dict)]


@pytest.fixture
async def sessions(settings: Settings) -> AsyncIterator[Sessions]:
    await asyncio.to_thread(migrate, settings.database)
    engine = create_engine(settings.database)
    try:
        yield create_sessions(engine)
    finally:
        await engine.dispose()


@pytest.fixture
def llm() -> Iterator[FakeLLM]:
    with FakeLLM(title="Scripted title") as server:
        yield server


@pytest.fixture
async def stand(
    settings: Settings, sessions: Sessions, s3: S3Client, llm: FakeLLM, tmp_path: Path
) -> AsyncIterator[Stand]:
    provider = settings.agent.providers["test"].model_copy(
        update={"base_url": llm.base_url, "api_key": SecretStr("test-key")}
    )
    # The Harness LLM plugin refuses a model whose only reasoning level is "off".
    model = settings.agent.models[0].model_copy(
        update={"reasoning_levels": {"off": None, "high": "high"}}
    )
    agent = settings.agent.model_copy(
        update={"cwd": tmp_path, "models": [model], "providers": {"test": provider}}
    )
    configured = settings.model_copy(update={"agent": agent})
    blobs = S3Adapter(s3, configured.s3.artifacts_bucket)
    bag = services(settings=configured, sessions=sessions, artifacts=Artifacts(sessions, blobs))
    gateway = Gateway(
        Registry([]),
        Executor(Actions(sessions), bag.artifacts, Redactor([])),
        bag,
        Listener(configured.database.dsn(), ACTIONS_CHANNEL),
        configured,
    )
    inputs = Listener(configured.database.dsn(), MESSAGES_CHANNEL)
    questions = Listener(configured.database.dsn(), QUESTIONS_CHANNEL)
    async with contextlib.AsyncExitStack() as stack:
        port = await stack.enter_async_context(serve(gateway.app(), "127.0.0.1", 0))
        gateway.base_url = f"http://127.0.0.1:{port}"
        for listener in (inputs, questions):
            await listener.start()
            stack.push_async_callback(listener.close)
        stack.push_async_callback(bag.fetcher.close)
        runner = Runner(
            configured,
            sessions,
            gateway,
            bag,
            blobs,
            inputs=inputs,
            questions=Questions(sessions, questions),
            redactor=Redactor([]),
        )
        yield Stand(runner, sessions, llm)


async def test_turn_records_the_answer_and_consumes_its_input(stand: Stand) -> None:
    stand.llm.queue(Reply(text="Hello from the scripted model."))
    turn = await stand.turn("Say hello")

    outcome = await stand.run(turn)

    assert outcome.status is TurnStatus.COMPLETED
    [answer] = await stand.assistant(turn)
    assert answer["text"] == "Hello from the scripted model."
    async with stand.sessions() as session:
        chat = await session.get_one(Chat, turn.chat_id)
        statuses = set(
            await session.scalars(select(Message.status).where(Message.chat_id == turn.chat_id))
        )
    assert statuses == {MessageStatus.CONSUMED}
    # The title plugin may skip a prompt; when it names the chat, the name is the model's.
    if chat.title_source is TitleSource.AUTO:
        assert chat.title == "Scripted title"


async def test_the_stored_final_response_is_redacted_like_the_feed(stand: Stand) -> None:
    secret = "sk-or-v1-abcdefghijklmnop1234"
    stand.llm.queue(Reply(text=f"Your key is {secret}."))
    turn = await stand.turn("Show the key")

    outcome = await stand.run(turn)

    assert outcome.status is TurnStatus.COMPLETED
    [answer] = await stand.assistant(turn)
    async with stand.sessions() as session:
        stored = await session.get_one(Turn, turn.turn_id)
    assert stored.final_response is not None
    assert secret not in stored.final_response
    assert REDACTED in stored.final_response
    assert stored.final_response == answer["text"]


async def test_new_input_interrupts_a_streaming_answer_and_keeps_its_prefix(
    stand: Stand,
) -> None:
    stand.llm.queue(Reply(text="word " * 40, chunks=40, delay=0.1))
    turn = await stand.turn("Write a long answer")
    running = asyncio.create_task(stand.run(turn))
    await asyncio.to_thread(stand.llm.wait_requests, 1)
    await asyncio.sleep(0.5)

    async with stand.sessions.begin() as session:
        await stand.queue(session, turn.chat_id, "Stop, do something else")
    outcome = await asyncio.wait_for(running, 30)

    assert outcome.status is TurnStatus.INTERRUPTED
    async with stand.sessions() as session:
        queued = list(
            await session.scalars(
                select(Message.text).where(
                    Message.chat_id == turn.chat_id, Message.status == MessageStatus.QUEUED
                )
            )
        )
    assert queued == ["Stop, do something else"]
    [answer] = await stand.assistant(turn)
    assert answer.get("interrupted") is True
    assert str(answer["text"]).startswith("word")
