import asyncio
import contextlib
import os
import sys
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal
from uuid import UUID

import pytest
from temporalio import activity
from temporalio.client import Client, WorkflowHandle
from temporalio.exceptions import ApplicationError
from temporalio.service import RPCError
from temporalio.worker import Replayer, Worker
from types_aiobotocore_s3.client import S3Client

from src.agent.notices import NoticeKind
from src.config import Settings
from src.db.models import ActionStatus, ChatStatus, PauseReason, TurnStatus, WorkspaceStatus
from src.orchestration.client import ChatCommands, connect_temporal
from src.orchestration.models import (
    ActionState,
    Activation,
    ApprovedRun,
    CancelActions,
    ChatView,
    ExpireActions,
    HibernateWorkspace,
    LoseWorkspace,
    Projection,
    ReadActions,
    SettleTurn,
    TurnInput,
    TurnLimit,
    TurnOutcome,
    WorkspaceView,
    host_queue,
)
from src.orchestration.workflows import ChatWorkflow
from src.policy.model import Risk

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

type Script = TurnOutcome | Literal["block", "crash", "slow"]
"""A scripted turn: an outcome, run until cancelled, fail the activity, or complete after 2 s."""

HOST = "host-a"
"""The worker the stand serves a host queue for."""


@dataclass
class FakeChat:
    """Activities of `ChatWorkflow` over in-memory chat state."""

    approval_ttl: timedelta = timedelta(hours=1)
    idle_ttl: timedelta = timedelta(hours=1)
    has_input: bool = False
    deleted: bool = False
    turns: list[Script] = field(default_factory=list)
    actions: dict[UUID, ActionState] = field(default_factory=dict)
    inputs: list[TurnInput] = field(default_factory=list)
    attempts: list[int] = field(default_factory=list)
    """Activity attempts of the turns this fake ran."""
    projections: list[Projection] = field(default_factory=list)
    settled: list[SettleTurn] = field(default_factory=list)
    cancelled: list[CancelActions] = field(default_factory=list)
    approved_runs: list[UUID] = field(default_factory=list)
    workspace: WorkspaceView = field(default_factory=WorkspaceView)
    workspace_idle_ttl: timedelta = timedelta(hours=1)
    restored_at: datetime | None = None
    log: list[str] = field(default_factory=list)
    """Activity calls as ``<name>@<task queue>``."""
    lost: list[LoseWorkspace] = field(default_factory=list)
    hibernations: list[HibernateWorkspace] = field(default_factory=list)

    def active_on(self, host: str) -> None:
        self.workspace = WorkspaceView(
            status=WorkspaceStatus.ACTIVE, host=host, epoch=1, touched_at=datetime.now(UTC)
        )

    def called(self, name: str) -> list[str]:
        """Task queues of the calls of activity ``name``."""
        return [entry.split("@", 1)[1] for entry in self.log if entry.split("@", 1)[0] == name]

    def record(self, name: str) -> None:
        self.log.append(f"{name}@{activity.info().task_queue}")

    def suspend(self) -> UUID:
        """Return the id of an action that the scripted turn listing it suspends."""
        return uuid.uuid4()

    def decide(self, action_id: UUID, status: ActionStatus) -> None:
        self.actions[action_id] = self.actions[action_id].model_copy(update={"status": status})

    @property
    def statuses(self) -> list[ChatStatus]:
        return [projection.status for projection in self.projections]

    def activities(self) -> list[Callable[..., object]]:
        return [
            self.ensure_chat,
            self.load_chat,
            self.project_chat,
            self.run_turn,
            self.settle_turn,
            self.run_approved_action,
            self.read_actions,
            self.expire_actions,
            self.cancel_actions,
            self.workspace_state,
            self.workspace_activate,
            self.workspace_checkpoint,
            self.workspace_hibernate,
            self.workspace_delete,
            self.workspace_lost,
            self.cancel_executions,
        ]

    @activity.defn(name="ensure_chat")
    async def ensure_chat(self, chat_id: UUID) -> None:
        pass

    @activity.defn(name="load_chat")
    async def load_chat(self, chat_id: UUID) -> ChatView:
        pending = (ActionStatus.SUSPENDED, ActionStatus.APPROVED)
        return ChatView(
            deleted=self.deleted,
            paused=False,
            pause_reason=None,
            has_input=self.has_input,
            pending=[state for state in self.actions.values() if state.status in pending],
            turn_timeout=timedelta(minutes=5),
            approval_ttl=self.approval_ttl,
            idle_ttl=self.idle_ttl,
            workspace_idle_ttl=self.workspace_idle_ttl,
            workspace=self.workspace,
        )

    @activity.defn(name="project_chat")
    async def project_chat(self, projection: Projection) -> None:
        self.projections.append(projection)
        self.deleted = projection.deleted

    @activity.defn(name="run_turn")
    async def run_turn(self, turn: TurnInput) -> TurnOutcome:
        self.has_input = False
        self.inputs.append(turn)
        self.attempts.append(activity.info().attempt)
        self.record("run_turn")
        script = self.turns.pop(0) if self.turns else TurnOutcome(status=TurnStatus.COMPLETED)
        match script:
            case "crash":
                raise ApplicationError("runtime crashed")
            case "block":
                while True:
                    activity.heartbeat()
                    await asyncio.sleep(0.1)
            case "slow":
                for _ in range(20):
                    activity.heartbeat()
                    await asyncio.sleep(0.1)
                self.record("turn_end")
                return TurnOutcome(status=TurnStatus.COMPLETED)
            case TurnOutcome():
                for action_id in script.suspended:
                    self.actions[action_id] = ActionState(
                        action_id=action_id,
                        tool="web_fetch",
                        status=ActionStatus.SUSPENDED,
                        risk=Risk.WRITE,
                        created_at=datetime.now(UTC),
                    )
                return script

    @activity.defn(name="settle_turn")
    async def settle_turn(self, settle: SettleTurn) -> None:
        self.settled.append(settle)

    @activity.defn(name="run_approved_action")
    async def run_approved_action(self, action_id: UUID) -> ApprovedRun:
        self.approved_runs.append(action_id)
        self.decide(action_id, ActionStatus.SUCCEEDED)
        return ApprovedRun(
            action_id=action_id, tool="web_fetch", status=ActionStatus.SUCCEEDED, result="ok"
        )

    @activity.defn(name="read_actions")
    async def read_actions(self, request: ReadActions) -> list[ActionState]:
        return [self.actions[action_id] for action_id in request.action_ids]

    @activity.defn(name="expire_actions")
    async def expire_actions(self, request: ExpireActions) -> None:
        for action_id, state in list(self.actions.items()):
            if state.status is ActionStatus.SUSPENDED:
                self.decide(action_id, ActionStatus.EXPIRED)

    @activity.defn(name="cancel_actions")
    async def cancel_actions(self, request: CancelActions) -> None:
        self.cancelled.append(request)
        for action_id, state in list(self.actions.items()):
            if state.status in {ActionStatus.SUSPENDED, ActionStatus.APPROVED}:
                self.decide(action_id, ActionStatus.CANCELLED)

    @activity.defn(name="workspace_state")
    async def workspace_state(self, chat_id: UUID) -> WorkspaceView:
        return self.workspace

    @activity.defn(name="workspace_activate")
    async def workspace_activate(self, chat_id: UUID) -> Activation:
        self.record("workspace_activate")
        self.active_on(HOST)
        return Activation(workspace=self.workspace, restored_at=self.restored_at)

    @activity.defn(name="workspace_checkpoint")
    async def workspace_checkpoint(self, chat_id: UUID) -> WorkspaceView:
        self.record("workspace_checkpoint")
        return self.workspace

    @activity.defn(name="workspace_hibernate")
    async def workspace_hibernate(self, request: HibernateWorkspace) -> WorkspaceView:
        self.record("workspace_hibernate")
        self.hibernations.append(request)
        self.workspace = WorkspaceView(status=WorkspaceStatus.HIBERNATED, epoch=1)
        return self.workspace

    @activity.defn(name="workspace_delete")
    async def workspace_delete(self, chat_id: UUID) -> WorkspaceView:
        self.record("workspace_delete")
        self.workspace = WorkspaceView(status=WorkspaceStatus.DELETED, epoch=1)
        return self.workspace

    @activity.defn(name="workspace_lost")
    async def workspace_lost(self, request: LoseWorkspace) -> WorkspaceView:
        self.record("workspace_lost")
        self.lost.append(request)
        self.workspace = WorkspaceView(status=WorkspaceStatus.HIBERNATED, epoch=request.epoch)
        return self.workspace

    @activity.defn(name="cancel_executions")
    async def cancel_executions(self, chat_id: UUID) -> None:
        self.record("cancel_executions")


@dataclass
class Stand:
    client: Client
    task_queue: str
    commands: ChatCommands
    fake: FakeChat
    chat_id: UUID

    def handle(self) -> WorkflowHandle[ChatWorkflow, None]:
        return self.client.get_workflow_handle_for(ChatWorkflow.run, str(self.chat_id))


@pytest.fixture
async def client(s3: S3Client, settings: Settings) -> Client:
    return await connect_temporal(settings, s3)


@pytest.fixture
def fake() -> FakeChat:
    return FakeChat()


@pytest.fixture
async def stand(client: Client, fake: FakeChat) -> AsyncIterator[Stand]:
    task_queue = f"test-chat-{uuid.uuid4()}"
    worker = Worker(
        client,
        task_queue=task_queue,
        workflows=[ChatWorkflow],
        activities=fake.activities(),
        max_heartbeat_throttle_interval=timedelta(milliseconds=200),
    )
    host = Worker(
        client,
        task_queue=host_queue(task_queue, HOST),
        activities=fake.activities(),
        max_heartbeat_throttle_interval=timedelta(milliseconds=200),
    )
    async with worker, host:
        stand = Stand(client, task_queue, ChatCommands(client, task_queue), fake, uuid.uuid4())
        yield stand
        with contextlib.suppress(RPCError):
            await stand.handle().terminate()


async def _eventually(check: Callable[[], bool], within: float = 15.0) -> None:
    async with asyncio.timeout(within):
        while not check():  # noqa: ASYNC110 - polls state of other tasks
            await asyncio.sleep(0.05)


async def test_input_runs_a_turn_and_returns_to_ready(stand: Stand) -> None:
    await stand.commands.input_available(stand.chat_id)

    await _eventually(lambda: stand.fake.statuses[-2:] == [ChatStatus.WORKING, ChatStatus.READY])
    assert len(stand.fake.inputs) == 1
    assert stand.fake.inputs[0].notices == []


async def test_input_queued_without_a_signal_is_found_on_start(stand: Stand) -> None:
    stand.fake.has_input = True

    await stand.client.start_workflow(
        ChatWorkflow.run, stand.chat_id, id=str(stand.chat_id), task_queue=stand.task_queue
    )

    await _eventually(lambda: len(stand.fake.inputs) == 1)


async def test_approved_action_runs_and_its_result_starts_a_turn(stand: Stand) -> None:
    action_id = stand.fake.suspend()
    stand.fake.turns.append(TurnOutcome(status=TurnStatus.SUSPENDED, suspended=[action_id]))
    await stand.commands.input_available(stand.chat_id)
    await _eventually(lambda: ChatStatus.NEEDS_APPROVAL in stand.fake.statuses)

    stand.fake.decide(action_id, ActionStatus.APPROVED)
    await stand.commands.action_decided(stand.chat_id, action_id)

    await _eventually(lambda: len(stand.fake.inputs) == 2)
    assert stand.fake.inputs[0].notices == []
    assert stand.fake.approved_runs == [action_id]
    [notice] = stand.fake.inputs[1].notices
    assert notice.kind is NoticeKind.APPROVAL_RESULT
    assert notice.data == {
        "actions": [
            {
                "action_id": str(action_id),
                "tool": "web_fetch",
                "status": "succeeded",
                "result": "ok",
            }
        ]
    }


async def test_rejected_action_is_reported_without_running(stand: Stand) -> None:
    action_id = stand.fake.suspend()
    stand.fake.turns.append(TurnOutcome(status=TurnStatus.SUSPENDED, suspended=[action_id]))
    await stand.commands.input_available(stand.chat_id)
    await _eventually(lambda: ChatStatus.NEEDS_APPROVAL in stand.fake.statuses)

    stand.fake.decide(action_id, ActionStatus.REJECTED)
    await stand.commands.action_decided(stand.chat_id, action_id)

    await _eventually(lambda: len(stand.fake.inputs) == 2)
    assert stand.fake.approved_runs == []
    assert stand.fake.inputs[1].notices[0].data == {
        "actions": [
            {"action_id": str(action_id), "tool": "web_fetch", "status": "rejected", "result": ""}
        ]
    }


@pytest.mark.parametrize("fake", [FakeChat(approval_ttl=timedelta(seconds=2))])
async def test_undecided_action_expires_after_the_ttl(stand: Stand) -> None:
    action_id = stand.fake.suspend()
    stand.fake.turns.append(TurnOutcome(status=TurnStatus.SUSPENDED, suspended=[action_id]))

    await stand.commands.input_available(stand.chat_id)

    await _eventually(lambda: len(stand.fake.inputs) == 2)
    assert stand.fake.actions[action_id].status is ActionStatus.EXPIRED
    assert stand.fake.inputs[1].notices[0].kind is NoticeKind.APPROVAL_RESULT


async def test_stop_cancels_the_turn_and_pending_actions(stand: Stand) -> None:
    stand.fake.turns.append("block")
    await stand.commands.input_available(stand.chat_id)
    await _eventually(lambda: len(stand.fake.inputs) == 1)

    result = await stand.commands.stop(stand.chat_id)

    assert result is not None
    assert result.status is ChatStatus.READY
    assert [settle.status for settle in stand.fake.settled] == [TurnStatus.STOPPED]
    assert stand.fake.cancelled == [CancelActions(chat_id=stand.chat_id)]


async def test_pause_holds_input_until_resume(stand: Stand) -> None:
    await stand.commands.input_available(stand.chat_id)
    await _eventually(lambda: stand.fake.statuses[-1:] == [ChatStatus.READY])

    paused = await stand.commands.pause(stand.chat_id)
    await stand.commands.input_available(stand.chat_id)
    await asyncio.sleep(1)

    assert paused is not None
    assert paused.status is ChatStatus.PAUSED
    assert paused.pause_reason is PauseReason.OPERATOR
    assert len(stand.fake.inputs) == 1

    assert await stand.commands.resume(stand.chat_id) is not None
    await _eventually(lambda: len(stand.fake.inputs) == 2)
    assert [notice.kind for notice in stand.fake.inputs[1].notices] == [NoticeKind.RESUMED]


async def test_budget_limit_pauses_the_chat(stand: Stand) -> None:
    stand.fake.turns.append(TurnOutcome(status=TurnStatus.LIMITED, limit=TurnLimit.BUDGET))

    await stand.commands.input_available(stand.chat_id)

    await _eventually(lambda: stand.fake.statuses[-1:] == [ChatStatus.PAUSED])
    assert stand.fake.projections[-1].pause_reason is PauseReason.BUDGET


async def test_step_limit_notice_rides_with_the_next_turn(stand: Stand) -> None:
    stand.fake.turns.append(TurnOutcome(status=TurnStatus.LIMITED, limit=TurnLimit.STEPS))
    await stand.commands.input_available(stand.chat_id)
    await _eventually(lambda: stand.fake.statuses[-2:] == [ChatStatus.WORKING, ChatStatus.READY])
    await asyncio.sleep(1)
    assert len(stand.fake.inputs) == 1

    await stand.commands.input_available(stand.chat_id)

    await _eventually(lambda: len(stand.fake.inputs) == 2)
    [notice] = stand.fake.inputs[1].notices
    assert notice.kind is NoticeKind.LIMIT_REACHED
    assert notice.data == {"limit": "steps"}


async def test_model_error_is_retried_once_with_a_recovery_notice(stand: Stand) -> None:
    error = TurnOutcome(status=TurnStatus.FAILED, reason="provider overloaded")
    stand.fake.turns.extend([error, error])

    await stand.commands.input_available(stand.chat_id)

    await _eventually(lambda: len(stand.fake.inputs) == 2, within=60)
    [notice] = stand.fake.inputs[1].notices
    assert notice.kind is NoticeKind.RECOVERY
    assert notice.data == {"reason": "model_error", "error": "provider overloaded"}
    await _eventually(lambda: stand.fake.statuses[-1:] == [ChatStatus.READY])
    await asyncio.sleep(1)
    assert len(stand.fake.inputs) == 2


async def test_turn_crashing_on_every_attempt_fails_without_a_model_retry(stand: Stand) -> None:
    stand.fake.turns.extend(["crash", "crash", "crash"])

    await stand.commands.input_available(stand.chat_id)

    await _eventually(lambda: stand.fake.settled != [], within=30)
    assert [settle.status for settle in stand.fake.settled] == [TurnStatus.FAILED]
    assert len(stand.fake.inputs) == 3
    await _eventually(lambda: stand.fake.statuses[-1:] == [ChatStatus.READY])


async def test_delete_stops_the_turn_and_completes_the_workflow(stand: Stand) -> None:
    stand.fake.active_on(HOST)
    stand.fake.turns.append("block")
    await stand.commands.input_available(stand.chat_id)
    await _eventually(lambda: len(stand.fake.inputs) == 1)

    result = await stand.commands.delete(stand.chat_id, stand.chat_id)

    assert result.status is ChatStatus.READY
    assert stand.fake.deleted
    await stand.handle().result()
    assert stand.fake.cancelled == [CancelActions(chat_id=stand.chat_id)]
    assert stand.fake.called("cancel_executions") == [stand.task_queue]
    assert stand.fake.called("workspace_hibernate") == [host_queue(stand.task_queue, HOST)]
    assert stand.fake.hibernations[-1].idle_ttl is None
    assert stand.fake.called("workspace_delete") == []
    assert await stand.commands.stop(stand.chat_id) is None
    assert (await stand.commands.delete(stand.chat_id, stand.chat_id)).status is ChatStatus.READY


@pytest.mark.parametrize("fake", [FakeChat(idle_ttl=timedelta(seconds=2))])
async def test_idle_chat_workflow_completes(stand: Stand) -> None:
    await stand.commands.input_available(stand.chat_id)

    async with asyncio.timeout(15):
        await stand.handle().result()
    assert stand.fake.statuses[-1] is ChatStatus.READY


async def test_turn_and_checkpoint_run_on_the_workspace_host(stand: Stand) -> None:
    stand.fake.active_on(HOST)

    await stand.commands.input_available(stand.chat_id)

    await _eventually(lambda: stand.fake.called("workspace_checkpoint") != [])
    queue = host_queue(stand.task_queue, HOST)
    assert stand.fake.called("run_turn") == [queue]
    assert stand.fake.called("workspace_checkpoint") == [queue]


@pytest.mark.parametrize("fake", [FakeChat(workspace_idle_ttl=timedelta(seconds=2))])
async def test_idle_workspace_hibernates_on_its_host(stand: Stand) -> None:
    stand.fake.active_on(HOST)

    await stand.commands.input_available(stand.chat_id)

    await _eventually(lambda: stand.fake.hibernations != [])
    assert stand.fake.called("workspace_hibernate") == [host_queue(stand.task_queue, HOST)]
    assert stand.fake.hibernations[0].idle_ttl == timedelta(seconds=2)


async def test_wake_during_a_turn_runs_after_it_and_notifies_the_next_turn(stand: Stand) -> None:
    stand.fake.turns.append("slow")
    stand.fake.restored_at = datetime(2026, 1, 1, tzinfo=UTC)
    await stand.commands.input_available(stand.chat_id)
    await _eventually(lambda: len(stand.fake.inputs) == 1)

    await stand.commands.workspace(stand.chat_id, "wake_workspace")

    await _eventually(lambda: stand.fake.called("workspace_activate") != [])
    names = [entry.split("@", 1)[0] for entry in stand.fake.log]
    assert names.index("turn_end") < names.index("workspace_activate")
    await stand.commands.input_available(stand.chat_id)
    await _eventually(lambda: len(stand.fake.inputs) == 2)
    [notice] = stand.fake.inputs[1].notices
    assert notice.kind is NoticeKind.WORKSPACE_RESTORED
    assert notice.data == {"checkpoint_at": "2026-01-01T00:00:00+00:00"}


async def test_explicit_hibernate_skips_the_idle_check(stand: Stand) -> None:
    stand.fake.active_on(HOST)
    await stand.commands.input_available(stand.chat_id)
    await _eventually(lambda: stand.fake.statuses[-1:] == [ChatStatus.READY])

    await stand.commands.workspace(stand.chat_id, "hibernate_workspace")

    await _eventually(lambda: stand.fake.hibernations != [])
    assert stand.fake.hibernations[0].idle_ttl is None


async def test_lost_host_moves_the_turn_to_the_shared_queue(stand: Stand) -> None:
    stand.fake.active_on("gone-host")

    await stand.commands.input_available(stand.chat_id)

    await _eventually(lambda: len(stand.fake.inputs) == 1, within=120)
    assert stand.fake.lost == [LoseWorkspace(chat_id=stand.chat_id, epoch=1)]
    assert stand.fake.called("run_turn") == [stand.task_queue]
    assert stand.fake.inputs[0].attempt_offset == 3


@pytest.mark.parametrize("fake", [FakeChat(turns=["slow", "slow"])])
async def test_parallel_chats_run_their_turns_at_once(stand: Stand) -> None:
    """AT-11: the turn of one chat does not wait for the turn of another."""
    other = uuid.uuid4()

    await stand.commands.input_available(stand.chat_id)
    await stand.commands.input_available(other)

    try:
        await _eventually(lambda: len(stand.fake.called("turn_end")) == 2)
    finally:
        await stand.client.get_workflow_handle(str(other)).terminate()
    names = [entry.split("@", 1)[0] for entry in stand.fake.log]
    turns = [name for name in names if name in {"run_turn", "turn_end"}]
    assert turns == ["run_turn", "run_turn", "turn_end", "turn_end"]


async def test_turns_of_many_chats_spread_over_two_workers(
    client: Client, s3: S3Client, settings: Settings
) -> None:
    """AT-17: two workers on one task queue share the turns; each holds at most two at once.

    Each worker has its own connection like a separate worker process: the SDK rejects two
    workers on one task queue over one connection.
    """
    task_queue = f"test-chat-{uuid.uuid4()}"
    fakes = (FakeChat(turns=["slow"] * 6), FakeChat(turns=["slow"] * 6))
    clients = (client, await connect_temporal(settings, s3))
    workers = [
        Worker(
            worker_client,
            task_queue=task_queue,
            workflows=[ChatWorkflow],
            activities=fake.activities(),
            max_concurrent_activities=2,
            max_heartbeat_throttle_interval=timedelta(milliseconds=200),
        )
        for worker_client, fake in zip(clients, fakes, strict=True)
    ]
    chats = [uuid.uuid4() for _ in range(6)]
    commands = ChatCommands(client, task_queue)

    async with workers[0], workers[1]:
        for chat_id in chats:
            await commands.input_available(chat_id)
        try:
            await _eventually(lambda: sum(len(fake.inputs) for fake in fakes) == 6, within=60)
        finally:
            for chat_id in chats:
                with contextlib.suppress(RPCError):
                    await client.get_workflow_handle(str(chat_id)).terminate()

    assert all(fake.inputs for fake in fakes)
    assert {turn.chat_id for fake in fakes for turn in fake.inputs} == set(chats)


async def test_turn_of_a_killed_worker_is_retried_by_its_restarted_worker(
    stand: Stand, settings: Settings
) -> None:
    """AT-13: the turn of a SIGKILLed worker times out its heartbeat and runs again."""
    host = "host-killed"
    queue = host_queue(stand.task_queue, host)
    stand.fake.active_on(host)
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(Path(__file__).with_name("turn_worker.py")),
        settings.temporal.address,
        settings.temporal.namespace,
        queue,
        stdout=asyncio.subprocess.PIPE,
    )
    assert process.stdout is not None
    try:
        async with asyncio.timeout(30):
            assert await process.stdout.readline() == b"ready\n"
            await stand.commands.input_available(stand.chat_id)
            assert (await process.stdout.readline()).startswith(b"running")
    finally:
        process.kill()
        await process.wait()

    async with Worker(
        stand.client,
        task_queue=queue,
        activities=stand.fake.activities(),
        max_heartbeat_throttle_interval=timedelta(milliseconds=200),
    ):
        await _eventually(lambda: stand.fake.attempts == [2], within=90)
        await _eventually(lambda: stand.fake.statuses[-1:] == [ChatStatus.READY])

    assert stand.fake.called("run_turn") == [queue]


HISTORIES = Path(__file__).parents[1] / "fixtures" / "histories" / "chat_workflow"
"""Recorded `ChatWorkflow` histories that later code must replay; see the replay test."""


async def test_chat_workflow_history_replays(stand: Stand) -> None:
    """Replay a history through approvals, a pause, a stop and the archive.

    ``XAVIER_RECORD_HISTORIES=1`` stores the history for `tests/unit/test_replay.py`, which
    replays every stored history against the current code.
    """
    action_id = stand.fake.suspend()
    stand.fake.turns.extend(
        [
            TurnOutcome(status=TurnStatus.SUSPENDED, suspended=[action_id]),
            TurnOutcome(status=TurnStatus.COMPLETED),
            "block",
        ]
    )
    await stand.commands.input_available(stand.chat_id)
    await _eventually(lambda: ChatStatus.NEEDS_APPROVAL in stand.fake.statuses)
    stand.fake.decide(action_id, ActionStatus.APPROVED)
    await stand.commands.action_decided(stand.chat_id, action_id)
    await _eventually(lambda: len(stand.fake.inputs) == 2)
    await _eventually(lambda: stand.fake.statuses[-1:] == [ChatStatus.READY])
    await stand.commands.pause(stand.chat_id)
    await stand.commands.resume(stand.chat_id)
    await stand.commands.input_available(stand.chat_id)
    await _eventually(lambda: len(stand.fake.inputs) == 3)
    await stand.commands.stop(stand.chat_id)
    await stand.commands.delete(stand.chat_id, stand.chat_id)
    await stand.handle().result()

    history = await stand.handle().fetch_history()
    await Replayer(
        workflows=[ChatWorkflow], data_converter=stand.client.data_converter
    ).replay_workflow(history)
    if os.environ.get("XAVIER_RECORD_HISTORIES") == "1":
        path = HISTORIES / "approval_pause_stop_delete.json"
        await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(path.write_text, history.to_json())
