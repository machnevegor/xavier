"""Body of the ``run_turn`` activity: one agent turn on a Harness runtime.

The runner fences the turn attempt, claims input, starts the runtime in a fresh Harness home
restored from the chat snapshot, streams events to PostgreSQL, enforces turn budgets and
finally stores the session snapshot, which is also the turn's checkpoint.

New operator input interrupts the turn at a safe point: the gateway refuses new calls and lets
calls with side effects finish, then the runtime shuts down gracefully, which records the
streamed prefix of the current response as an interrupted message. The next turn resumes the
session with the new input.
"""

import asyncio
import base64
import contextlib
import json
import logging
import posixpath
import shutil
import tempfile
import threading
import time
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

from deepseek_harness import DeepSeekHarness, JsonObject, Notification, UserQuestionRequest
from deepseek_harness.client import NotificationSubscription
from deepseek_harness.errors import HarnessError
from pydantic import JsonValue
from sqlalchemy import exists, func, select, update
from sqlalchemy.dialects.postgresql import insert

from src.agent import capabilities, embeds, snapshots
from src.agent import harness_events as he
from src.agent.gateway import (
    Actions,
    Gateway,
    StaleAttemptError,
    TurnHandle,
    TurnState,
    chat_scope,
)
from src.agent.notices import Notice, NoticeKind, current, render
from src.agent.profile import harness_config
from src.agent.questions import TOOL as QUESTION_TOOL
from src.agent.questions import WAIT_FOREVER, Questions, Waited
from src.config import ModelSettings, Settings
from src.db.engine import Sessions
from src.db.models import (
    Action,
    ActionStatus,
    Chat,
    Message,
    MessageStatus,
    TitleSource,
    ToolGroup,
    Turn,
    TurnStatus,
    TurnTrigger,
    Wakeup,
    WakeupStatus,
)
from src.events import EventBatcher, Listener, NewEvent, append_events
from src.orchestration.models import TurnInput, TurnLimit, TurnOutcome, WakeupView
from src.policy.model import Risk
from src.policy.redact import Redactor
from src.storage.blobs import BlobStore
from src.telemetry import LLM_COST, LLM_TOKENS, TURN_DURATION, tracer
from src.tools.base import Services
from src.workspace.service import WorkspaceError
from src.workspace.volumes import WORKSPACE_MOUNT

logger = logging.getLogger(__name__)

HEARTBEAT_SECONDS = 5.0
_POLL_SECONDS = 1.0
_INPUT_RECHECK_SECONDS = 5.0
_QUESTION_CALL_WAIT = 10.0
"""Time the question handler waits for the runner to see the tool call it belongs to."""
_INTERRUPTED = {ActionStatus.RUNNING, ActionStatus.AWAITING_APPROVAL, ActionStatus.APPROVED}
_WORKSPACE_GROUPS = {ToolGroup.EXECUTE.value, ToolGroup.COMPUTE.value}
_FRESH_CAPABILITIES = {
    NoticeKind.LIMITS_CHANGED,
    NoticeKind.HISTORY_REWOUND,
    NoticeKind.APPROVAL_RESULT,
}
SESSION_LOG = "session.v4.jsonl"
INBOX = f"{WORKSPACE_MOUNT}/inbox"
"""Where message attachments are written."""

type Heartbeat = Callable[[], None]


class RuntimeCrashedError(Exception):
    """The Harness runtime failed to start or exited while the turn was running."""


_CRASH_CHARS = 1000
"""Temporal rejects large failures; the full runtime output goes to the log."""
_CRASH_LINES = ("exit code:", "Error:")


def runtime_crash(error: BaseException) -> RuntimeCrashedError:
    """Log ``error`` in full and return a crash with its summary.

    The summary keeps the first line of the message, the exit code and the runtime's first
    ``Error:`` line from its stderr tail.
    """
    logger.error("Harness runtime failed: %s", error)
    lines = [line.strip() for line in str(error).splitlines() if line.strip()]
    first = lines[0] if lines else type(error).__name__
    found = [next((line for line in lines if line.startswith(p)), "") for p in _CRASH_LINES]
    summary = "; ".join(dict.fromkeys(line for line in (first, *found) if line))
    return RuntimeCrashedError(summary[:_CRASH_CHARS])


@dataclass(slots=True)
class _Progress:
    """What the runner has observed during the turn."""

    steps: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cost_usd: float = 0.0
    text: str = ""
    """Text of the last assistant message."""
    end_reason: str | None = None
    spliced: bool = False
    open_calls: set[str] = field(default_factory=set)
    last_seq: int | None = None
    """Session log sequence of the last event received."""
    partial: bool = False

    def charge(self, usage: he.Usage, model: ModelSettings) -> None:
        self.input_tokens += usage.input
        self.output_tokens += usage.output
        self.cache_read_tokens += usage.cache_read
        self.cost_usd += _cost(model, usage)


@dataclass(slots=True)
class _Questions:
    """Native question calls of the turn."""

    unclaimed: dict[str, tuple[JsonValue, int]] = field(default_factory=dict)
    """Tool calls not yet matched to a host request: questions and wait by call id."""
    ids: dict[str, UUID] = field(default_factory=dict)
    """Stored question by call id."""
    waiting: set[UUID] = field(default_factory=set)
    """Questions the turn suspends on."""
    seen: asyncio.Condition = field(default_factory=asyncio.Condition)

    async def called(self, call_id: str, arguments: str, default_timeout: int) -> None:
        """Record an ``ask_user_question`` call for the host request that follows it."""
        async with self.seen:
            self.unclaimed[call_id] = _question_call(arguments, default_timeout)
            self.seen.notify_all()

    def calls(self) -> set[str]:
        """Return the ids of question calls, which never hold an interrupt or a suspension."""
        return self.ids.keys() | self.unclaimed.keys()


@dataclass(slots=True)
class _Turn:
    """One running turn attempt."""

    turn: TurnInput
    attempt: int
    chat: Chat
    model: ModelSettings
    handle: TurnHandle
    home: Path
    budget_usd: float
    """What the turn may spend."""
    max_steps: int | None
    """Step limit copied from the chat when the attempt started. ``None`` sets no step limit."""
    progress: _Progress = field(default_factory=_Progress)
    questions: _Questions = field(default_factory=_Questions)
    message_id: str = ""
    """Id of the input message in the Harness session."""
    stopped: bool = False


@dataclass(frozen=True, slots=True)
class _Ending:
    outcome: TurnOutcome
    keep_decisions: bool = True


class Runner:
    """Run agent turns on this worker."""

    def __init__(  # noqa: PLR0913 - the stores and channels a turn uses
        self,
        settings: Settings,
        sessions: Sessions,
        gateway: Gateway,
        services: Services,
        blobs: BlobStore,
        *,
        inputs: Listener,
        questions: Questions,
        redactor: Redactor,
    ) -> None:
        self._settings = settings
        self._sessions = sessions
        self._gateway = gateway
        self._artifacts = services.artifacts
        self._workspaces = services.workspaces
        self._credentials = services.credentials
        self._blobs = blobs
        self._actions = Actions(sessions)
        self._redact = redactor
        self._inputs = inputs
        self._questions = questions
        self._question_timeout = int(settings.approval.hot_wait.total_seconds())
        """Wait of question calls that name none; the profile sets it as the native default."""
        self._cache = snapshots.BlobCache(settings.agent.cwd / "snapshot-cache")

    async def run(self, turn: TurnInput, attempt: int, heartbeat: Heartbeat) -> TurnOutcome:
        """Run attempt ``attempt`` of the turn; see `_attempt`."""
        started = time.monotonic()
        status = "failed"
        attributes = {"xavier.chat_id": str(turn.chat_id), "xavier.turn_id": str(turn.turn_id)}
        with tracer.start_as_current_span("xavier.turn", attributes=attributes):
            try:
                outcome = await self._attempt(turn, attempt, heartbeat)
            except asyncio.CancelledError:
                status = TurnStatus.STOPPED.value
                raise
            finally:
                TURN_DURATION.record(time.monotonic() - started, {"outcome": status})
            status = outcome.status.value
            return outcome

    async def _attempt(self, turn: TurnInput, attempt: int, heartbeat: Heartbeat) -> TurnOutcome:
        """Run attempt ``attempt`` of the turn.

        Cancellation stops the turn: hot waits end as ``suspended``, the turn is stored as
        ``stopped`` and `asyncio.CancelledError` propagates.

        Raises
        ------
        StaleAttemptError
            If a newer attempt owns the turn.
        RuntimeCrashedError
            If the runtime failed to start or exited by itself; the attempt may be retried.
        """
        chat, interrupted = await self._fence(turn, attempt)
        model = self._settings.agent.model(chat.model)
        budget = chat.budget_usd - chat.spent_usd
        if turn.trigger is not TurnTrigger.INPUT:
            budget = min(budget, self._settings.proactive.budget_usd_per_wake)
        if budget <= 0:
            exhausted = chat.budget_usd <= chat.spent_usd
            limit = TurnLimit.BUDGET if exhausted else TurnLimit.TURN_BUDGET
            outcome = TurnOutcome(status=TurnStatus.LIMITED, limit=limit)
            await self._finish(turn, attempt, outcome, _Progress(), manifest=None)
            return outcome
        blocks = await self._input(turn, attempt, chat, model, interrupted)
        scope = chat_scope(
            chat, turn_id=turn.turn_id, attempt=attempt, image_input="image" in model.input
        )
        handle = self._gateway.register(scope)
        home = Path(tempfile.mkdtemp(prefix="xavier-turn-"))
        beating = asyncio.create_task(_beat(heartbeat))
        try:
            return await self._run(
                _Turn(turn, attempt, chat, model, handle, home, budget, chat.max_steps), blocks
            )
        finally:
            beating.cancel()
            self._gateway.revoke(handle)
            await asyncio.to_thread(shutil.rmtree, home, ignore_errors=True)

    async def _run(self, run: _Turn, blocks: list[JsonObject]) -> TurnOutcome:
        loop = asyncio.get_running_loop()

        def on_question(request: UserQuestionRequest, cancelled: threading.Event) -> JsonObject:
            future = asyncio.run_coroutine_threadsafe(self._ask(run, request, cancelled), loop)
            return future.result()

        config = harness_config(
            self._settings.agent,
            run.model,
            reasoning=run.chat.reasoning,
            dsh_home=run.home,
            mcp_url=run.handle.url,
            question_timeout=self._question_timeout,
            on_user_question=on_question,
        )
        harness = DeepSeekHarness(config)
        session_id = run.turn.chat_id.hex
        manifest = run.chat.snapshot_manifest
        closing = threading.Event()
        try:
            restore = (
                snapshots.restore(
                    self._blobs, run.turn.chat_id, manifest, run.home, cache=self._cache
                )
                if manifest
                else asyncio.sleep(0)
            )
            await asyncio.gather(restore, asyncio.to_thread(harness.start))
            if manifest:
                await asyncio.to_thread(harness.client.resume_session, session_id)
            queue = _pump(harness.client.subscribe_session_notifications(session_id), closing)
            async with (
                EventBatcher(self._sessions, run.turn.chat_id) as feed,
                self._watch_input(run),
            ):
                run.message_id = await asyncio.to_thread(
                    harness.client.session_prompt, session_id, blocks
                )
                ending = await self._drive(run, queue, feed)
                if ending.outcome.status is TurnStatus.INTERRUPTED:
                    await self._stop(run, harness, closing, keep_decisions=True)
                    await self._record_tail(run, feed)
        except asyncio.CancelledError:
            await self._stop(run, harness, closing, keep_decisions=True)
            await self._settle(run, TurnOutcome(status=TurnStatus.STOPPED, reason="stopped"))
            raise
        except HarnessError as error:
            await self._stop(run, harness, closing, keep_decisions=True)
            raise runtime_crash(error) from None
        except BaseException:
            await self._stop(run, harness, closing, keep_decisions=True)
            raise
        await self._stop(run, harness, closing, keep_decisions=ending.keep_decisions)
        outcome = ending.outcome
        if outcome.status is TurnStatus.SUSPENDED:
            outcome = outcome.model_copy(
                update={
                    "suspended": sorted(run.handle.suspended),
                    "questions": sorted(run.questions.waiting),
                }
            )
        if outcome.status is TurnStatus.INTERRUPTED:
            superseded = await self._actions.supersede(run.handle.suspended)
            run.handle.suspended.clear()
            outcome = outcome.model_copy(
                update={
                    "superseded": sorted(run.handle.superseded | superseded),
                    "partial": run.progress.partial,
                }
            )
        return await self._settle(run, outcome)

    async def _settle(self, run: _Turn, outcome: TurnOutcome) -> TurnOutcome:
        """Upload the session snapshot, store the outcome and report queued input."""
        progress, model = run.progress, {"model": run.model.id}
        LLM_TOKENS.add(progress.input_tokens, {**model, "kind": "input"})
        LLM_TOKENS.add(progress.output_tokens, {**model, "kind": "output"})
        LLM_TOKENS.add(progress.cache_read_tokens, {**model, "kind": "cache_read"})
        LLM_COST.add(progress.cost_usd, model)
        manifest = await snapshots.upload(
            self._blobs,
            run.turn.chat_id,
            run.turn.turn_id,
            run.attempt,
            run.home,
            cache=self._cache,
        )
        stored = await self._finish(run.turn, run.attempt, outcome, run.progress, manifest=manifest)
        if stored and manifest is not None:
            try:
                await snapshots.prune(
                    self._blobs, run.turn.chat_id, await self._referenced(run.turn.chat_id)
                )
            except Exception:
                # The outcome is stored: a failed cleanup must not fail the turn; the next
                # turn prunes again.
                logger.exception("pruning snapshots of chat %s failed", run.turn.chat_id)
        async with self._sessions() as session:
            proactive = await session.scalar(
                select(Chat.proactive).where(Chat.id == run.turn.chat_id)
            )
            wakeups = (
                await session.execute(
                    select(Wakeup.id, Wakeup.due_at)
                    .where(
                        Wakeup.chat_id == run.turn.chat_id, Wakeup.status == WakeupStatus.PENDING
                    )
                    .order_by(Wakeup.due_at)
                )
            ).all()
        return outcome.model_copy(
            update={
                "pending_input": await self._has_input(run.turn.chat_id),
                "proactive": bool(proactive),
                "wakeups": [WakeupView(wakeup_id=w_id, due_at=due) for w_id, due in wakeups],
            }
        )

    async def _referenced(self, chat_id: UUID) -> set[str]:
        """Return the snapshot manifests turns and the chat refer to; revisions rewind to them."""
        async with self._sessions() as session:
            turns = await session.scalars(
                select(Turn.checkpoint_manifest).where(
                    Turn.chat_id == chat_id, Turn.checkpoint_manifest.is_not(None)
                )
            )
            current = await session.scalar(select(Chat.snapshot_manifest).where(Chat.id == chat_id))
        return {key for key in [*turns, current] if key}

    async def _drive(
        self, run: _Turn, queue: asyncio.Queue[Notification | BaseException], feed: EventBatcher
    ) -> _Ending:
        """Consume runtime events until the turn ends, yields to input or a limit stops it."""
        session_id = run.turn.chat_id.hex
        progress, handle = run.progress, run.handle
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._settings.turn.timeout.total_seconds()
        while True:
            ending = self._stopping(run, deadline - loop.time())
            if ending is not None:
                return ending
            try:
                item = await asyncio.wait_for(queue.get(), _POLL_SECONDS)
            except TimeoutError:
                continue
            if isinstance(item, BaseException):
                raise runtime_crash(item) from None
            event = he.parse(item, session_id)
            progress.last_seq = he.sequence(item) or progress.last_seq
            if event is None:
                continue
            await self._observe(run, event, feed)
            if isinstance(event, he.SessionIdle) and progress.spliced:
                return _settled(handle, progress)

    def _stopping(self, run: _Turn, remaining: float) -> _Ending | None:
        """Return how the turn ends now, if a limit, an interrupt or a suspension stops it.

        Interrupts and suspensions wait until no call with possible side effects is in flight;
        question calls do not hold them.
        """
        progress, handle = run.progress, run.handle
        limit = self._limit(run, remaining)
        if limit is not None:
            budget = limit in {TurnLimit.BUDGET, TurnLimit.TURN_BUDGET}
            return _limited(limit, keep_decisions=budget)
        if handle.in_flight or progress.open_calls - run.questions.calls():
            return None
        if handle.state is TurnState.INTERRUPTING:
            return _Ending(TurnOutcome(status=TurnStatus.INTERRUPTED))
        if handle.state is TurnState.SUSPENDING or run.questions.waiting:
            return _Ending(TurnOutcome(status=TurnStatus.SUSPENDED))
        return None

    def _limit(self, run: _Turn, remaining: float) -> TurnLimit | None:
        progress = run.progress
        if run.max_steps is not None and progress.steps > run.max_steps:
            return TurnLimit.STEPS
        if remaining <= 0:
            return TurnLimit.TIME
        if progress.cost_usd < run.budget_usd:
            return None
        capped = run.budget_usd < run.chat.budget_usd - run.chat.spent_usd
        return TurnLimit.TURN_BUDGET if capped else TurnLimit.BUDGET

    async def _observe(self, run: _Turn, event: he.HarnessEvent, feed: EventBatcher) -> None:
        progress = run.progress
        match event:
            case he.StepStart():
                progress.steps += 1
            case he.AssistantMessage():
                await self._respond(run, event, feed)
            case he.SessionTitle(title=title, generated=True) if title.strip():
                await self._set_title(run.turn.chat_id, title.strip())
            case he.ToolCall(call_id=call_id, name=name, arguments=arguments):
                progress.open_calls.add(call_id)
                if name == QUESTION_TOOL:
                    await run.questions.called(call_id, arguments, self._question_timeout)
            case he.ToolResult(call_id=call_id, text=text):
                progress.open_calls.discard(call_id)
                question_id = run.questions.ids.get(call_id)
                if question_id is not None and _pending(text):
                    await self._questions.undelivered(question_id)
            case he.TurnEnd(reason=reason):
                progress.end_reason = reason
                _log_turn_error(run.turn.turn_id, event)
            case he.InboxSpliced(message_ids=ids):
                progress.spliced = progress.spliced or run.message_id in ids
            case _:
                pass

    async def _respond(self, run: _Turn, message: he.AssistantMessage, feed: EventBatcher) -> None:
        progress = run.progress
        progress.text = message.text
        if message.usage is not None:
            progress.charge(message.usage, run.model)
        if not message.text:
            return
        progress.partial = progress.partial or message.interrupted
        redacted = self._redact(message.text)
        data: dict[str, JsonValue] = {"text": redacted}
        if message.interrupted:
            data["interrupted"] = True
        if _WORKSPACE_GROUPS & set(run.chat.tool_groups):
            try:
                found = await embeds.resolve(
                    redacted, run.turn.chat_id, self._workspaces, self._artifacts
                )
            except Exception:
                logger.exception("resolving embeds of chat %s failed", run.turn.chat_id)
                found = {}
            if found:
                data["embeds"] = found
        media = embeds.image_urls(redacted)
        if media:
            data["media"] = list(media)
        feed.add(NewEvent("assistant", data, run.turn.turn_id))

    async def _set_title(self, chat_id: UUID, title: str) -> None:
        """Store a generated title unless the operator named the chat."""
        title = title[:200]
        async with self._sessions.begin() as session:
            updated = await session.scalar(
                update(Chat)
                .where(Chat.id == chat_id, Chat.title_source != TitleSource.USER)
                .values(title=title, title_source=TitleSource.AUTO)
                .returning(Chat.id)
            )
            if updated is not None:
                await append_events(
                    session, chat_id, [NewEvent("title", {"title": title, "source": "auto"})]
                )

    async def _ask(
        self, run: _Turn, request: UserQuestionRequest, cancelled: threading.Event
    ) -> JsonObject:
        """Answer a native question request: store it, show it and wait for the operator."""
        call_id, questions, timeout = await self._claim_call(run, request)
        question_id = await self._questions.open(
            chat_id=run.turn.chat_id,
            turn_id=run.turn.turn_id,
            attempt=run.attempt,
            call_id=call_id,
            questions=questions,
            timeout_s=timeout,
        )
        if question_id is None:
            await _until(cancelled)
            return {"answers": []}
        run.questions.ids[call_id] = question_id
        waits = timeout == WAIT_FOREVER
        outcome = await self._questions.wait(
            question_id,
            cancelled,
            suspend_after=self._settings.approval.hot_wait.total_seconds() if waits else None,
        )
        if outcome.waited is Waited.ANSWERED and isinstance(outcome.answers, list):
            return {"answers": outcome.answers}
        if outcome.waited is Waited.SUSPENDED:
            run.questions.waiting.add(question_id)
            if run.handle.state is TurnState.OPEN:
                run.handle.state = TurnState.SUSPENDING
            run.handle.changed.set()
        await _until(cancelled)
        return {"answers": []}

    async def _claim_call(
        self, run: _Turn, request: UserQuestionRequest
    ) -> tuple[str, JsonValue, int]:
        """Return the tool call a host request belongs to: same question ids, oldest first."""
        ids = [item.get("id") for item in request.questions]

        def match() -> str | None:
            for call_id, (questions, _timeout) in run.questions.unclaimed.items():
                if (
                    isinstance(questions, list)
                    and [q.get("id") if isinstance(q, dict) else None for q in questions] == ids
                ):
                    return call_id
            return None

        async with run.questions.seen:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    run.questions.seen.wait_for(lambda: match() is not None), _QUESTION_CALL_WAIT
                )
            call_id = match()
            if call_id is None:
                logger.warning("no tool call matches question request %s", request.request_id)
                return request.request_id, list(request.questions), self._question_timeout
            questions, timeout = run.questions.unclaimed.pop(call_id)
            return call_id, questions, timeout

    @contextlib.asynccontextmanager
    async def _watch_input(self, run: _Turn) -> AsyncGenerator[None]:
        """Interrupt the turn when operator input is queued for the chat."""
        chat_id = run.turn.chat_id

        async def watch() -> None:
            async with self._inputs.subscribe() as payloads:
                while not await self._has_input(chat_id):
                    with contextlib.suppress(TimeoutError):
                        while await asyncio.wait_for(
                            payloads.get(), _INPUT_RECHECK_SECONDS
                        ) not in {str(chat_id), None}:
                            pass
            self._gateway.interrupt(run.handle)

        task = asyncio.create_task(watch())
        try:
            yield
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _has_input(self, chat_id: UUID) -> bool:
        async with self._sessions() as session:
            return bool(
                await session.scalar(
                    select(
                        exists().where(
                            Message.chat_id == chat_id, Message.status == MessageStatus.QUEUED
                        )
                    )
                )
            )

    async def _record_tail(self, run: _Turn, feed: EventBatcher) -> None:
        """Add the events the runtime logged while it shut down: the interrupted response."""
        logs = await asyncio.to_thread(lambda: list(run.home.rglob(SESSION_LOG)))
        for log in logs:
            data = await asyncio.to_thread(log.read_bytes)
            for event in he.logged_events(data, run.progress.last_seq):
                if isinstance(event, he.AssistantMessage | he.StepStart):
                    await self._observe(run, event, feed)

    async def _stop(
        self,
        run: _Turn,
        harness: DeepSeekHarness,
        closing: threading.Event,
        *,
        keep_decisions: bool,
    ) -> None:
        if run.stopped:
            return
        run.stopped = True
        await self._gateway.stop(run.handle, keep_decisions=keep_decisions)
        closing.set()
        await asyncio.to_thread(harness.close)
        for question_id in run.questions.ids.values():
            if question_id not in run.questions.waiting:
                await self._questions.release(question_id)

    async def _fence(
        self, turn: TurnInput, attempt: int
    ) -> tuple[Chat, list[dict[str, JsonValue]]]:
        """Take the turn for ``attempt`` and settle actions and questions of earlier attempts."""
        async with self._sessions.begin() as session:
            await session.execute(
                insert(Turn)
                .values(
                    id=turn.turn_id,
                    chat_id=turn.chat_id,
                    status=TurnStatus.RUNNING,
                    trigger=turn.trigger,
                )
                .on_conflict_do_nothing()
            )
            owned = await session.scalar(
                update(Turn)
                .where(Turn.id == turn.turn_id, Turn.attempt < attempt)
                .values(attempt=attempt, worker_id=self._settings.worker.id)
                .returning(Turn.id)
            )
            if owned is None:
                raise StaleAttemptError(str(turn.turn_id))
            chat = await session.get_one(Chat, turn.chat_id)
            previous = (
                await session.execute(
                    select(Action.id, Action.tool, Action.status, Action.risk).where(
                        Action.turn_id == turn.turn_id,
                        Action.attempt < attempt,
                        Action.status.in_(list(_INTERRUPTED)),
                    )
                )
            ).all()
            await append_events(
                session,
                turn.chat_id,
                [NewEvent("turn", {"status": "running", "attempt": attempt}, turn.turn_id)],
            )
        await self._questions.cancel_stale(turn.turn_id, attempt)
        interrupted: list[dict[str, JsonValue]] = []
        for action_id, tool, status, risk in previous:
            if status is ActionStatus.RUNNING:
                new = ActionStatus.FAILED if risk is Risk.READ else ActionStatus.UNCERTAIN
            else:
                new = ActionStatus.CANCELLED
            await self._actions.transition(action_id, {status}, new, error_code="interrupted")
            interrupted.append({"action_id": str(action_id), "tool": tool, "status": new.value})
        return chat, interrupted

    async def _input(
        self,
        turn: TurnInput,
        attempt: int,
        chat: Chat,
        model: ModelSettings,
        interrupted: list[dict[str, JsonValue]],
    ) -> list[JsonObject]:
        """Claim the queued messages and return the content blocks of the turn."""
        async with self._sessions.begin() as session:
            await session.execute(
                update(Message)
                .where(Message.chat_id == turn.chat_id, Message.status == MessageStatus.QUEUED)
                .values(status=MessageStatus.CONSUMED, turn_id=turn.turn_id)
            )
            messages = (
                await session.scalars(
                    select(Message).where(Message.turn_id == turn.turn_id).order_by(Message.id)
                )
            ).all()
            notices = await current(session, turn.notices)
        await self._questions.stop_waiting(turn.chat_id)
        if attempt > 1:
            notices.append(
                Notice(kind=NoticeKind.RECOVERY, data={"attempt": attempt, "actions": interrupted})
            )
        if chat.snapshot_manifest is None or _FRESH_CAPABILITIES & {n.kind for n in notices}:
            data = capabilities.describe(chat)
            data["credentials"] = list(await self._credentials.available(chat.id, chat.created_by))
            notices.append(Notice(kind=NoticeKind.CAPABILITIES, data=data))
        blocks: list[JsonObject] = [render(notice) for notice in notices]
        for message in messages:
            if message.text:
                blocks.append({"type": "text", "text": message.text})
            for artifact_id in message.image_artifact_ids:
                if "image" not in model.input:
                    blocks.append({"type": "text", "text": "[image omitted: unsupported]"})
                    continue
                artifact, data_bytes = await self._artifacts.read(artifact_id, chat.created_by)
                blocks.append(
                    {
                        "type": "image",
                        "data": base64.b64encode(data_bytes).decode("ascii"),
                        "mimeType": artifact.content_type,
                    }
                )
            blocks.extend([await self._attach(chat, a) for a in message.file_artifact_ids])
        return blocks

    async def _attach(self, chat: Chat, artifact_id: UUID) -> JsonObject:
        """Write a message attachment into the workspace inbox and describe it."""
        artifact, data = await self._artifacts.read(artifact_id, chat.created_by)
        if not _WORKSPACE_GROUPS & set(chat.tool_groups):
            note = f"[file {artifact.name!r} omitted: no workspace tools are enabled]"
            return {"type": "text", "text": note}
        path = f"{INBOX}/{posixpath.basename(artifact.name) or artifact.id}"
        try:
            active = await self._workspaces.activate(chat.id)
            await self._workspaces.write_file(active, path, data)
        except WorkspaceError as error:
            note = f"[file {artifact.name!r} not delivered: {error.message}]"
            return {"type": "text", "text": note}
        await self._workspaces.touch(chat.id)
        return {"type": "text", "text": f"[attachment] {path} ({len(data)} bytes)"}

    async def _finish(
        self,
        turn: TurnInput,
        attempt: int,
        outcome: TurnOutcome,
        progress: _Progress,
        *,
        manifest: str | None,
    ) -> bool:
        """Store the outcome, usage and snapshot pointer if the attempt still owns the turn."""
        final = (
            self._redact(progress.text)
            if outcome.status is TurnStatus.COMPLETED and progress.text
            else None
        )
        async with self._sessions.begin() as session:
            owned = await session.scalar(
                update(Turn)
                .where(Turn.id == turn.turn_id, Turn.attempt == attempt)
                .values(
                    status=outcome.status,
                    reason=outcome.reason or (outcome.limit.value if outcome.limit else None),
                    final_response=final,
                    steps=progress.steps,
                    input_tokens=progress.input_tokens,
                    output_tokens=progress.output_tokens,
                    cache_read_tokens=progress.cache_read_tokens,
                    cost_usd=progress.cost_usd,
                    checkpoint_manifest=manifest,
                    finished_at=func.now(),
                )
                .returning(Turn.id)
            )
            if owned is None:
                logger.warning("turn %s attempt %d lost ownership", turn.turn_id, attempt)
                return False
            values: dict[str, object] = {"spent_usd": Chat.spent_usd + progress.cost_usd}
            if manifest is not None:
                values["snapshot_manifest"] = manifest
            await session.execute(update(Chat).where(Chat.id == turn.chat_id).values(**values))
            await append_events(
                session,
                turn.chat_id,
                [
                    NewEvent(
                        "turn",
                        {
                            "status": outcome.status.value,
                            "reason": outcome.reason,
                            "limit": outcome.limit.value if outcome.limit else None,
                            "final_response": final,
                            "steps": progress.steps,
                            "cost_usd": progress.cost_usd,
                        },
                        turn.turn_id,
                    )
                ],
            )
        return True


def _question_call(arguments: str, default_timeout: int) -> tuple[JsonValue, int]:
    """Return the questions and the wait of an ``ask_user_question`` call."""
    try:
        parsed = json.loads(arguments)
    except json.JSONDecodeError:
        return [], default_timeout
    if not isinstance(parsed, dict):
        return [], default_timeout
    timeout = parsed.get("timeout")
    return parsed.get("questions", []), timeout if isinstance(timeout, int) else default_timeout


def _pending(text: str) -> bool:
    """Whether a question tool result is the native pending result, not an answer."""
    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        return False
    return isinstance(result, dict) and result.get("pending") is True


async def _until(event: threading.Event) -> None:
    while not event.is_set():  # noqa: ASYNC110 - the SDK sets a threading.Event
        await asyncio.sleep(0.2)


def _log_turn_error(turn_id: UUID, end: he.TurnEnd) -> None:
    if end.error is not None:
        logger.warning("turn %s ended with %s: %s", turn_id, end.reason, end.error)


def _settled(handle: TurnHandle, progress: _Progress) -> _Ending:
    if handle.suspended:
        return _Ending(TurnOutcome(status=TurnStatus.SUSPENDED))
    if progress.end_reason == "completed":
        return _Ending(TurnOutcome(status=TurnStatus.COMPLETED))
    return _Ending(TurnOutcome(status=TurnStatus.FAILED, reason=progress.end_reason))


def _limited(limit: TurnLimit, *, keep_decisions: bool = False) -> _Ending:
    return _Ending(
        TurnOutcome(status=TurnStatus.LIMITED, limit=limit), keep_decisions=keep_decisions
    )


def _cost(model: ModelSettings, usage: he.Usage) -> float:
    prices = model.pricing_per_mtok
    cache_price = prices.input if prices.cache_read is None else prices.cache_read
    return (
        usage.input * prices.input + usage.output * prices.output + usage.cache_read * cache_price
    ) / 1_000_000


async def _beat(heartbeat: Heartbeat) -> None:
    while True:
        heartbeat()
        await asyncio.sleep(HEARTBEAT_SECONDS)


def _pump(
    subscription: NotificationSubscription, closing: threading.Event
) -> asyncio.Queue[Notification | BaseException]:
    """Forward runtime notifications from a thread into an asyncio queue.

    A transport failure is forwarded unless the runner itself is closing the runtime.
    """
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[Notification | BaseException] = asyncio.Queue()

    def forward() -> None:
        while True:
            try:
                item: Notification | BaseException = subscription.next()
            except Exception as error:  # noqa: BLE001 - forwarded to the runner
                if not closing.is_set():
                    with contextlib.suppress(RuntimeError):
                        loop.call_soon_threadsafe(queue.put_nowait, error)
                return
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(queue.put_nowait, item)

    threading.Thread(target=forward, name="harness-events", daemon=True).start()
    return queue
