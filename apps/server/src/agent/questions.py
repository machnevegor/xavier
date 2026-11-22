"""Questions of the native ``ask_user_question`` tool: storage, answers and late delivery.

The runtime asks the host through ``on_user_question`` while the tool call waits. The runner
stores the question, shows it in the feed and returns the operator's answer if it arrives in
time; the runtime then records it as the tool result. A question answered after its call stopped
waiting reaches the agent as the native late reply: a user message with the JSON
``{"kind": "answer_to_pending_question", "tool": "ask_user_question", "callId": ...}``.

``live`` marks a question whose tool call still waits in a running turn; an answer to a question
that is not live becomes a queued message. Both sides change a question under its row lock, so
every answer is delivered exactly once.
"""

import asyncio
import json
import threading
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import ColumnElement, exists, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.engine import Sessions
from src.db.models import (
    Message,
    MessageKind,
    MessageStatus,
    Question,
    QuestionStatus,
    Turn,
)
from src.events import (
    MESSAGES_CHANNEL,
    QUESTIONS_CHANNEL,
    Listener,
    NewEvent,
    append_events,
    notify,
)

TOOL = "ask_user_question"
WAIT_FOREVER = -1

_POLL_SECONDS = 0.25
_RECHECK_SECONDS = 5.0


class InvalidAnswerError(ValueError):
    """The answer does not fit the questions."""


class QuestionClosedError(Exception):
    """The question no longer accepts an answer; ``status`` says why."""

    def __init__(self, status: QuestionStatus) -> None:
        super().__init__(f"the question is {status.value}")
        self.status = status


class Waited(StrEnum):
    """How the wait of a live question ended."""

    ANSWERED = "answered"
    RELEASED = "released"
    """The tool call stopped waiting; a later answer becomes a message."""
    SUSPENDED = "suspended"
    """An ask-and-wait question outlasted the hot wait: the turn suspends until the answer."""


@dataclass(frozen=True, slots=True)
class Outcome:
    """End of a live wait."""

    waited: Waited
    answers: JsonValue = None


def late_answer_text(call_id: str, questions: JsonValue, answers: JsonValue) -> str:
    """Return the native late reply the runtime itself would add for an answer."""
    return json.dumps(
        {
            "kind": "answer_to_pending_question",
            "tool": TOOL,
            "callId": call_id,
            "questions": questions,
            "answers": answers,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def validate_answers(questions: JsonValue, answers: Sequence[dict[str, JsonValue]]) -> JsonValue:
    """Return the answers in native form after checking them against the questions.

    Every question needs one item. ``selected`` holds option labels: one at most unless the
    question allows several. ``custom`` is free text; for a single-select question it replaces
    the selection. An item with neither skips the question.

    Raises
    ------
    InvalidAnswerError
        If an item does not fit its question.
    """
    items = (
        {str(q["id"]): q for q in questions if isinstance(q, dict)}
        if isinstance(questions, list)
        else {}
    )
    given: dict[str, dict[str, JsonValue]] = {}
    for answer in answers:
        question_id = str(answer.get("id"))
        if question_id not in items:
            raise InvalidAnswerError(f"unknown question id {question_id!r}")
        if question_id in given:
            raise InvalidAnswerError(f"question {question_id!r} is answered twice")
        given[question_id] = answer
    missing = sorted(set(items) - set(given))
    if missing:
        raise InvalidAnswerError(f"answers are missing for {', '.join(missing)}")
    return [_native_answer(id_, question, given[id_]) for id_, question in items.items()]


def _native_answer(
    question_id: str, question: dict[str, JsonValue], answer: dict[str, JsonValue]
) -> JsonValue:
    selected = answer.get("selected") or []
    custom = answer.get("custom")
    if not isinstance(selected, list) or not all(isinstance(s, str) for s in selected):
        raise InvalidAnswerError(f"`selected` of {question_id!r} must be a list of labels")
    options = question.get("options")
    labels = {
        str(option.get("label"))
        for option in (options if isinstance(options, list) else [])
        if isinstance(option, dict)
    }
    unknown = [label for label in selected if label not in labels]
    if unknown:
        raise InvalidAnswerError(f"{question_id!r} has no option {unknown[0]!r}")
    multi = question.get("multi_select") is True or question.get("multiSelect") is True
    if len(selected) > 1 and not multi:
        raise InvalidAnswerError(f"{question_id!r} accepts one option")
    if custom is not None and not isinstance(custom, str):
        raise InvalidAnswerError(f"`custom` of {question_id!r} must be text")
    item: dict[str, JsonValue] = {
        "id": question_id,
        "selected": [] if custom and not multi else list(selected),
    }
    if custom:
        item["custom"] = custom
    return item


def question_event(question: Question) -> NewEvent:
    """Return the feed event of the question's current state."""
    return NewEvent(
        "question",
        {
            "question_id": str(question.id),
            "call_id": question.call_id,
            "questions": question.questions,
            "timeout_s": question.timeout_s,
            "status": question.status.value,
            "answers": question.answers,
            "waiting": question.waiting,
        },
        question.turn_id,
    )


async def queue_late_answer(session: AsyncSession, question: Question) -> Message:
    """Queue the late reply of an answered question that no tool call waits for."""
    message = Message(
        id=uuid.uuid7(),
        chat_id=question.chat_id,
        text=late_answer_text(question.call_id, question.questions, question.answers),
        image_artifact_ids=[],
        file_artifact_ids=[],
        status=MessageStatus.QUEUED,
        kind=MessageKind.ANSWER,
        question_id=question.id,
    )
    session.add(message)
    await notify(session, MESSAGES_CHANNEL, str(question.chat_id))
    return message


async def answer(
    sessions: Sessions, question_id: UUID, answers: Sequence[dict[str, JsonValue]], ttl: timedelta
) -> tuple[Question, bool]:
    """Store the operator's answer; return the question and whether it waits as a queued message.

    Repeating the same answer is safe.

    Raises
    ------
    LookupError
        If the question does not exist.
    InvalidAnswerError
        If the answer does not fit the questions.
    QuestionClosedError
        If the question was answered differently, expired or was cancelled.
    """
    async with sessions.begin() as session:
        question = await session.get(Question, question_id, with_for_update=True)
        if question is None:
            raise LookupError(str(question_id))
        native = validate_answers(question.questions, answers)
        if question.status is QuestionStatus.ANSWERED and question.answers == native:
            undelivered = await session.scalar(
                select(
                    exists().where(
                        Message.question_id == question.id, Message.status == MessageStatus.QUEUED
                    )
                )
            )
            return question, bool(undelivered)
        if question.status is not QuestionStatus.PENDING:
            raise QuestionClosedError(question.status)
        now = datetime.now(UTC)
        if question.created_at + ttl > now:
            question.status = QuestionStatus.ANSWERED
            question.answers = native
            question.answered_at = now
            queued = not question.live
            if queued:
                question.waiting = False
                await queue_late_answer(session, question)
            await append_events(session, question.chat_id, [question_event(question)])
            await notify(session, QUESTIONS_CHANNEL, str(question.id))
            return question, queued
        question.status = QuestionStatus.EXPIRED
        question.live = question.waiting = False
        await append_events(session, question.chat_id, [question_event(question)])
    raise QuestionClosedError(QuestionStatus.EXPIRED)


class Questions:
    """Questions as the runner handles them."""

    def __init__(self, sessions: Sessions, listener: Listener) -> None:
        self._sessions = sessions
        self._listener = listener

    async def open(  # noqa: PLR0913 - the fields of the asked call
        self,
        *,
        chat_id: UUID,
        turn_id: UUID,
        attempt: int,
        call_id: str,
        questions: JsonValue,
        timeout_s: int,
    ) -> UUID | None:
        """Store a live question of the turn attempt; ``None`` when a newer attempt owns it."""
        async with self._sessions.begin() as session:
            current = await session.scalar(
                select(Turn.attempt).where(Turn.id == turn_id).with_for_update(read=True)
            )
            if current != attempt:
                return None
            question = Question(
                id=uuid.uuid7(),
                chat_id=chat_id,
                turn_id=turn_id,
                attempt=attempt,
                call_id=call_id,
                questions=questions,
                timeout_s=timeout_s,
                status=QuestionStatus.PENDING,
                live=True,
                waiting=False,
            )
            session.add(question)
            await session.flush()
            await append_events(session, chat_id, [question_event(question)])
            return question.id

    async def wait(
        self, question_id: UUID, cancelled: threading.Event, *, suspend_after: float | None
    ) -> Outcome:
        """Wait for the answer while the tool call waits.

        The wait ends when the operator answers, when the runtime stops waiting (``cancelled``)
        or, with ``suspend_after``, after that many seconds: the question then marks the turn
        as waiting for input.
        """
        loop = asyncio.get_running_loop()
        deadline = None if suspend_after is None else loop.time() + suspend_after
        async with self._listener.subscribe() as notifications:
            while True:
                async with self._sessions() as session:
                    question = await session.get_one(Question, question_id)
                if question.status is QuestionStatus.ANSWERED and not cancelled.is_set():
                    await self.delivered(question_id)
                    return Outcome(Waited.ANSWERED, question.answers)
                if question.status is not QuestionStatus.PENDING or cancelled.is_set():
                    await self.release(question_id)
                    return Outcome(Waited.RELEASED)
                if deadline is not None and loop.time() >= deadline:
                    if await self._release(question_id, waiting=True):
                        return Outcome(Waited.SUSPENDED)
                    continue
                within = _RECHECK_SECONDS if deadline is None else deadline - loop.time()
                await _next(notifications, cancelled, max(0.0, min(within, _RECHECK_SECONDS)))

    async def release(self, question_id: UUID) -> None:
        """End the live wait; an answer that arrived meanwhile is queued as a late reply."""
        await self._release(question_id, waiting=False)

    async def _release(self, question_id: UUID, *, waiting: bool) -> bool:
        """Mark the question as no longer live; return whether it is still unanswered.

        An answer stored while the question was live is queued as a late reply here, since the
        answering request left its delivery to the tool call.
        """
        async with self._sessions.begin() as session:
            question = await session.get_one(Question, question_id, with_for_update=True)
            if not question.live:
                return question.status is QuestionStatus.PENDING
            question.live = False
            if question.status is QuestionStatus.ANSWERED:
                await queue_late_answer(session, question)
            if question.status is not QuestionStatus.PENDING:
                return False
            question.waiting = waiting
            if waiting:
                await append_events(session, question.chat_id, [question_event(question)])
            return True

    async def delivered(self, question_id: UUID) -> None:
        """Record that the runtime received the answer as the tool result."""
        async with self._sessions.begin() as session:
            await session.execute(
                update(Question).where(Question.id == question_id).values(live=False)
            )

    async def undelivered(self, question_id: UUID) -> None:
        """Queue an answer the runtime dropped because its wait ended at the same moment."""
        async with self._sessions.begin() as session:
            question = await session.get_one(Question, question_id, with_for_update=True)
            if question.status is not QuestionStatus.ANSWERED:
                return
            delivered = await session.scalar(
                select(Message.id).where(Message.question_id == question_id).limit(1)
            )
            if delivered is None:
                await queue_late_answer(session, question)

    async def cancel_stale(self, turn_id: UUID, attempt: int) -> None:
        """Cancel the unanswered questions of earlier attempts: their tool calls are gone."""
        await self._cancel(Question.turn_id == turn_id, Question.attempt < attempt)

    async def stop_waiting(self, chat_id: UUID) -> None:
        """Clear the waiting mark: a turn runs, so the chat no longer waits for an answer."""
        async with self._sessions.begin() as session:
            await session.execute(
                update(Question)
                .where(Question.chat_id == chat_id, Question.waiting.is_(True))
                .values(waiting=False)
            )

    async def _cancel(self, *conditions: ColumnElement[bool]) -> None:
        async with self._sessions.begin() as session:
            await cancel_questions(session, *conditions)


async def cancel_questions(session: AsyncSession, *conditions: ColumnElement[bool]) -> list[UUID]:
    """Cancel the unanswered questions that match ``conditions``; return their ids."""
    rows = (
        await session.scalars(
            update(Question)
            .where(Question.status == QuestionStatus.PENDING, *conditions)
            .values(status=QuestionStatus.CANCELLED, live=False, waiting=False)
            .returning(Question)
        )
    ).all()
    for question in rows:
        await append_events(session, question.chat_id, [question_event(question)])
    return [question.id for question in rows]


async def _next(
    notifications: asyncio.Queue[str | None], cancelled: threading.Event, within: float
) -> None:
    """Return on a notification, on ``cancelled`` or after ``within`` seconds."""
    loop = asyncio.get_running_loop()
    end = loop.time() + within
    while not cancelled.is_set():
        remaining = end - loop.time()
        if remaining <= 0:
            return
        try:
            await asyncio.wait_for(notifications.get(), min(_POLL_SECONDS, remaining))
        except TimeoutError:
            continue
        return
