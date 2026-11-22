"""Temporal workflows.

`ChatWorkflow` (id = chat id), `ExecutionWorkflow`, `ObservePollWorkflow`, `ChatScheduleWorkflow`
and `MaintenanceWorkflow`.
"""

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from uuid import UUID

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import (
    ActivityError,
    ApplicationError,
    CancelledError,
    TimeoutType,
)
from temporalio.exceptions import TimeoutError as ActivityTimeoutError

with workflow.unsafe.imports_passed_through():
    from src.agent.notices import Notice, NoticeKind
    from src.db.models import (
        ActionStatus,
        ChatStatus,
        PauseReason,
        TurnStatus,
        TurnTrigger,
        WorkspaceStatus,
    )
    from src.observe.service import PollEnd, ResourceOutcome, ResourceTask
    from src.orchestration.activities import (
        STALE_ATTEMPT,
        WORKSPACE_ERRORS,
        ChatActivities,
        MaintenanceActivities,
        ObserveActivities,
        ScheduleActivities,
        WorkspaceActivities,
    )
    from src.orchestration.models import (
        ActionState,
        AdmitEvents,
        ApprovedRun,
        CancelActions,
        ChatScheduleRun,
        ChatView,
        CommandResult,
        DeleteChat,
        EditMessage,
        ExecutionDone,
        ExecutionStart,
        ExpireActions,
        HibernateWorkspace,
        LoseWorkspace,
        ObserveStep,
        Projection,
        ReadActions,
        RewindRequest,
        Rewound,
        ScheduledMessage,
        SettleExecution,
        SettleTurn,
        TurnInput,
        TurnLimit,
        TurnOutcome,
        WorkspaceView,
        host_queue,
    )
    from src.policy.model import Risk

CHAT_DELETED = "chat_deleted"
TURN_ATTEMPTS = 3
ATTEMPTS_EXHAUSTED = "attempts_exhausted"
MODEL_RETRY_DELAY = timedelta(seconds=30)
RECONCILE_INTERVAL = timedelta(minutes=10)
_SHORT = timedelta(seconds=60)
_TURN_MARGIN = timedelta(minutes=10)
_APPROVED_RUN = timedelta(minutes=5)
_SETTLED = frozenset({ChatStatus.READY, ChatStatus.PAUSED})
HOST_SCHEDULE_TO_START = timedelta(seconds=60)
"""A host-bound task not picked up within this time means the host is lost."""
IDLE_RECHECK = timedelta(minutes=1)
"""Next idle check of a workspace that could not be hibernated."""
EXECUTION_ATTEMPTS = 3
_WORKSPACE_OP = timedelta(minutes=30)
_WORKSPACE_RETRY = RetryPolicy(maximum_attempts=3, non_retryable_error_types=list(WORKSPACE_ERRORS))
_EXECUTION_MARGIN = timedelta(minutes=2)
_MAINTENANCE_OP = timedelta(minutes=30)
_OBSERVE_STEP = timedelta(minutes=5)
_WEBHOOK_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=10), backoff_coefficient=2.0, maximum_attempts=5
)
MAX_POLL_RESOURCES = 1000
"""Resources one poll fetches at most, bounding its history."""

type HostCall[T] = Callable[[str | None, timedelta | None], Awaitable[T]]
"""An activity call given the task queue and ``schedule_to_start`` timeout to use."""


def _uuid7() -> UUID:
    """Return a time-ordered UUID from workflow time and randomness."""
    millis = int(workflow.now().timestamp() * 1000) & ((1 << 48) - 1)
    rand = workflow.random().getrandbits(74)
    value = (millis << 80) | (0x7 << 76) | ((rand >> 62) << 64) | (0b10 << 62)
    return UUID(int=value | (rand & ((1 << 62) - 1)))


@workflow.defn(name="ChatWorkflow")
class ChatWorkflow:
    """Chat status, one turn at a time, durable approvals and operator commands."""

    _view: ChatView

    @workflow.init
    def __init__(self, chat_id: UUID) -> None:
        self._chat_id = chat_id
        self._status: ChatStatus | None = None
        self._pause_reason: PauseReason | None = None
        self._version = 0
        self._paused = False
        self._stop = False
        self._deleting: DeleteChat | None = None
        self._deleted = False
        self._input = False
        self._decided = False
        self._suspended: set[UUID] = set()
        self._notices: list[Notice] = []
        """Notices that start a turn."""
        self._carry: list[Notice] = []
        """Notices delivered with the next turn, which they do not start."""
        self._model_retry = False
        self._turn: workflow.ActivityHandle[TurnOutcome] | None = None
        self._workspace = WorkspaceView()
        self._idle_check_at: datetime | None = None
        self._wake = False
        self._hibernate = False
        self._delete = False
        self._questions: set[UUID] = set()
        """Questions a suspended turn waits for."""
        self._proactive = False
        self._wakeups: dict[UUID, datetime] = {}
        self._events: list[Notice] = []
        """Events that start a turn in Proactive mode once admitted."""
        self._trigger = TurnTrigger.INPUT
        self._edit: EditMessage | None = None
        self._edits = 0
        self._rewound: dict[int, Rewound] = {}

    @workflow.run
    async def run(self, chat_id: UUID) -> None:
        """Serve the chat until it is deleted or idle for ``chat.idle_ttl``."""
        await workflow.execute_activity_method(
            ChatActivities.ensure_chat, chat_id, start_to_close_timeout=_SHORT
        )
        self._view = await self._load()
        if self._view.deleted:
            self._deleted = True
            await workflow.wait_condition(workflow.all_handlers_finished)
            return
        self._restore(self._view)
        while await self._step():
            pass
        await workflow.wait_condition(workflow.all_handlers_finished)

    def _restore(self, view: ChatView) -> None:
        self._paused = self._paused or view.paused
        self._pause_reason = self._pause_reason or view.pause_reason
        self._input = self._input or view.has_input
        self._suspended |= {action.action_id for action in view.pending}
        self._questions |= set(view.questions)
        self._proactive = view.proactive
        self._wakeups = {w.wakeup_id: w.due_at for w in view.wakeups}
        self._workspace = view.workspace

    async def _step(self) -> bool:
        """Do what the chat needs next; return ``False`` when the workflow ends."""
        if self._deleting is not None:
            await self._delete_chat(self._deleting)
            return False
        await self._serve_workspace()
        if self._edit is not None:
            await self._rewind(self._edit)
        elif self._stop:
            await self._stop_chat()
        elif self._paused:
            await self._wait_paused()
        elif self._suspended:
            await self._wait_decisions()
        elif self._questions and not (self._input or self._notices):
            await self._wait_answers()
        elif self._input or self._notices:
            await self._run_turn()
        elif self._events:
            await self._admit_events()
        else:
            return await self._wait_ready()
        return True

    @workflow.signal(name="input_available")
    def input_available(self) -> None:
        """Record that new messages are queued."""
        self._input = True

    @workflow.signal(name="settings_changed")
    def settings_changed(self, proactive: bool) -> None:  # noqa: FBT001 - signal argument
        """Apply changed chat settings; the next turn learns its new capabilities."""
        self._proactive = proactive
        if not proactive:
            self._wakeups.clear()
            self._carry += self._events
            self._events = []
        self._carry.append(Notice(kind=NoticeKind.LIMITS_CHANGED))

    @workflow.signal(name="chat_event")
    def chat_event(self, notice: Notice) -> None:
        """Report a finished job or an alert: it starts a turn only in Proactive mode."""
        if self._proactive:
            self._events.append(notice)
        else:
            self._carry.append(notice)

    @workflow.update(name="edit_message")
    async def edit_message(self, edit: EditMessage) -> Rewound:
        """Revise an operator message and answer it again from the checkpoint before it.

        A running turn is stopped first. Later turns stay in the history as superseded.
        """
        await workflow.wait_condition(lambda: self._edit is None)
        self._edits += 1
        number = self._edits
        self._edit = edit
        self._cancel_turn()
        await workflow.wait_condition(lambda: number in self._rewound)
        return self._rewound.pop(number)

    @edit_message.validator
    def _validate_edit(self, edit: EditMessage) -> None:
        del edit
        self._reject_deleted()

    @workflow.signal(name="action_decided")
    def action_decided(self, action_id: UUID) -> None:
        """Record an operator decision; the decision itself is read from PostgreSQL."""
        if action_id in self._suspended:
            self._decided = True

    @workflow.signal(name="wake_workspace")
    def wake_workspace(self) -> None:
        """Activate the workspace; during a turn this happens after it."""
        self._wake = True

    @workflow.signal(name="hibernate_workspace")
    def hibernate_workspace(self) -> None:
        """Checkpoint and release the workspace unless executions use it."""
        self._hibernate = True

    @workflow.signal(name="delete_workspace")
    def delete_workspace(self) -> None:
        """Delete the workspace volumes and checkpoints."""
        self._delete = True

    @workflow.query(name="state")
    def state(self) -> CommandResult:
        """Return the chat status."""
        return self._result()

    @workflow.update(name="pause")
    async def pause(self) -> CommandResult:
        """Stop the turn, keep pending decisions and start no turns until ``resume``."""
        if not self._paused:
            self._paused = True
            self._pause_reason = PauseReason.OPERATOR
            self._cancel_turn()
        await workflow.wait_condition(lambda: self._status is ChatStatus.PAUSED or self._deleted)
        return self._result()

    @workflow.update(name="resume")
    async def resume(self) -> CommandResult:
        """Lift the pause; queued input or decisions start a turn with the ``resumed`` notice."""
        if self._paused:
            self._paused = False
            self._pause_reason = None
            if self._input or self._suspended:
                self._notices.append(Notice(kind=NoticeKind.RESUMED))
            await workflow.wait_condition(lambda: self._status is not ChatStatus.PAUSED)
        return self._result()

    @workflow.update(name="stop")
    async def stop(self) -> CommandResult:
        """Stop the turn and cancel unfinished actions and pending decisions."""
        self._stop = True
        self._cancel_turn()
        await workflow.wait_condition(lambda: not self._stop and self._status in _SETTLED)
        return self._result()

    @workflow.update(name="delete")
    async def delete(self, request: DeleteChat) -> CommandResult:
        """Delete the chat: stop it for good, then release its workspace into hibernation."""
        if self._deleting is None and not self._deleted:
            self._deleting = request
            self._cancel_turn()
        await workflow.wait_condition(lambda: self._deleted)
        return self._result()

    @pause.validator
    def _validate_pause(self) -> None:
        self._reject_deleted()

    @resume.validator
    def _validate_resume(self) -> None:
        self._reject_deleted()

    @stop.validator
    def _validate_stop(self) -> None:
        self._reject_deleted()

    def _reject_deleted(self) -> None:
        if self._deleting is not None or self._deleted:
            raise ApplicationError("the chat is deleted", type=CHAT_DELETED)

    def _result(self) -> CommandResult:
        return CommandResult(
            status=self._status or ChatStatus.READY, pause_reason=self._pause_reason
        )

    def _commands(self) -> bool:
        return self._stop or self._deleting is not None or self._paused or self._edit is not None

    def _cancel_turn(self) -> None:
        if self._turn is not None:
            self._turn.cancel()

    async def _run_turn(self) -> None:
        """Run a turn on the workspace host; a lost host moves it to the shared queue."""
        turn_id = _uuid7()
        notices, self._notices, self._carry = [*self._notices, *self._carry], [], []
        trigger = TurnTrigger.INPUT if self._input else self._trigger
        self._input = False
        self._trigger = TurnTrigger.INPUT
        self._questions.clear()
        await self._project(ChatStatus.WORKING)
        offset = 0
        while True:
            queue, start = self._placement()
            outcome = await self._turn_attempts(
                TurnInput(
                    chat_id=self._chat_id,
                    turn_id=turn_id,
                    notices=notices,
                    attempt_offset=offset,
                    trigger=trigger,
                ),
                queue,
                start,
            )
            if outcome is not None:
                break
            await self._lose_workspace()
            offset += TURN_ATTEMPTS
        await self._after_turn()
        await self._apply(outcome)

    async def _turn_attempts(
        self, turn: TurnInput, queue: str | None, start: timedelta | None
    ) -> TurnOutcome | None:
        """Run the attempts of a turn on ``queue``; ``None`` when its host is lost."""
        self._turn = workflow.start_activity_method(
            ChatActivities.run_turn,
            turn,
            task_queue=queue,
            schedule_to_start_timeout=start,
            start_to_close_timeout=self._view.turn_timeout + _TURN_MARGIN,
            heartbeat_timeout=timedelta(seconds=30),
            retry_policy=RetryPolicy(
                maximum_attempts=TURN_ATTEMPTS, non_retryable_error_types=[STALE_ATTEMPT]
            ),
            cancellation_type=workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
        )
        try:
            return await self._turn
        except ActivityError as error:
            if queue is not None and _host_lost(error):
                return None
            stopped = isinstance(error.cause, CancelledError)
            status = TurnStatus.STOPPED if stopped else TurnStatus.FAILED
            await workflow.execute_activity_method(
                ChatActivities.settle_turn,
                SettleTurn(turn_id=turn.turn_id, status=status),
                start_to_close_timeout=_SHORT,
            )
            return TurnOutcome(status=status, reason=None if stopped else ATTEMPTS_EXHAUSTED)
        finally:
            self._turn = None

    async def _after_turn(self) -> None:
        """Refresh the workspace state and checkpoint an active workspace."""
        self._workspace = await workflow.execute_activity_method(
            WorkspaceActivities.workspace_state, self._chat_id, start_to_close_timeout=_SHORT
        )
        if self._workspace.status is not WorkspaceStatus.ACTIVE:
            return
        view = await self._on_host(
            lambda queue, start: workflow.execute_activity_method(
                WorkspaceActivities.workspace_checkpoint,
                self._chat_id,
                task_queue=queue,
                schedule_to_start_timeout=start,
                start_to_close_timeout=_WORKSPACE_OP,
                retry_policy=_WORKSPACE_RETRY,
            )
        )
        if view is not None:
            self._workspace = view

    def _placement(self) -> tuple[str | None, timedelta | None]:
        """Return the queue and ``schedule_to_start`` of host-bound work; shared when inactive."""
        workspace = self._workspace
        if workspace.status is WorkspaceStatus.ACTIVE and workspace.host:
            queue = host_queue(workflow.info().task_queue, workspace.host)
            return queue, HOST_SCHEDULE_TO_START
        return None, None

    async def _on_host[T](self, call: HostCall[T]) -> T | None:
        """Run a workspace operation where the workspace is.

        A lost host releases the workspace and returns ``None``; other failures are logged and
        return ``None`` so that the chat keeps serving.
        """
        queue, start = self._placement()
        try:
            return await call(queue, start)
        except ActivityError as error:
            if queue is not None and _host_lost(error):
                await self._lose_workspace()
            else:
                workflow.logger.warning("workspace operation failed: %s", error.cause or error)
            return None

    async def _lose_workspace(self) -> None:
        lost = self._workspace
        self._workspace = await workflow.execute_activity_method(
            WorkspaceActivities.workspace_lost,
            LoseWorkspace(chat_id=self._chat_id, epoch=lost.epoch),
            start_to_close_timeout=_SHORT,
        )
        if self._workspace.epoch == lost.epoch and self._workspace.status is not lost.status:
            self._carry.append(
                Notice(
                    kind=NoticeKind.WORKSPACE_FAILED,
                    data={
                        "reason": "the worker hosting the workspace was lost; running processes "
                        "ended and the next use restores the last checkpoint"
                    },
                )
            )

    async def _serve_workspace(self) -> None:
        """Handle workspace requests and the idle timer; never runs during a turn."""
        if self._deleting is not None:
            return
        if self._delete:
            self._delete = self._wake = self._hibernate = False
            await self._delete_workspace()
        if self._hibernate or self._workspace_due():
            idle_ttl = None if self._hibernate else self._view.workspace_idle_ttl
            self._hibernate = False
            await self._hibernate_workspace(idle_ttl)
        if self._wake:
            self._wake = False
            await self._activate_workspace()

    def _workspace_deadline(self) -> datetime | None:
        workspace = self._workspace
        if workspace.status is not WorkspaceStatus.ACTIVE or workspace.touched_at is None:
            return None
        deadline = workspace.touched_at + self._view.workspace_idle_ttl
        if self._idle_check_at is not None:
            deadline = max(deadline, self._idle_check_at)
        return deadline

    def _workspace_due(self) -> bool:
        deadline = self._workspace_deadline()
        return deadline is not None and workflow.now() >= deadline

    def _workspace_requested(self) -> bool:
        return self._wake or self._hibernate or self._delete or self._workspace_due()

    async def _hibernate_workspace(self, idle_ttl: timedelta | None) -> None:
        if self._workspace.status is not WorkspaceStatus.ACTIVE:
            return
        request = HibernateWorkspace(chat_id=self._chat_id, idle_ttl=idle_ttl)
        view = await self._on_host(
            lambda queue, start: workflow.execute_activity_method(
                WorkspaceActivities.workspace_hibernate,
                request,
                task_queue=queue,
                schedule_to_start_timeout=start,
                start_to_close_timeout=_WORKSPACE_OP,
                retry_policy=_WORKSPACE_RETRY,
            )
        )
        if view is not None:
            self._workspace = view
        self._idle_check_at = workflow.now() + IDLE_RECHECK

    async def _activate_workspace(self) -> None:
        for _ in range(2):
            activation = await self._on_host(
                lambda queue, start: workflow.execute_activity_method(
                    WorkspaceActivities.workspace_activate,
                    self._chat_id,
                    task_queue=queue,
                    schedule_to_start_timeout=start,
                    start_to_close_timeout=_WORKSPACE_OP,
                    retry_policy=_WORKSPACE_RETRY,
                )
            )
            if activation is None:
                if self._workspace.status is WorkspaceStatus.ACTIVE:
                    return
                continue
            self._workspace = activation.workspace
            if activation.restored_at is not None:
                self._carry.append(
                    Notice(
                        kind=NoticeKind.WORKSPACE_RESTORED,
                        data={"checkpoint_at": activation.restored_at.isoformat()},
                    )
                )
            return

    async def _delete_workspace(self) -> None:
        for _ in range(2):
            view = await self._on_host(
                lambda queue, start: workflow.execute_activity_method(
                    WorkspaceActivities.workspace_delete,
                    self._chat_id,
                    task_queue=queue,
                    schedule_to_start_timeout=start,
                    start_to_close_timeout=_WORKSPACE_OP,
                    retry_policy=_WORKSPACE_RETRY,
                )
            )
            if view is not None:
                self._workspace = view
                return
            if self._workspace.status is WorkspaceStatus.ACTIVE:
                return

    async def _apply(self, outcome: TurnOutcome) -> None:
        model_error = outcome.status is TurnStatus.FAILED and outcome.reason != ATTEMPTS_EXHAUSTED
        retry = model_error and not self._model_retry
        self._model_retry = retry
        self._input = self._input or outcome.pending_input
        if outcome.wakeups is not None:
            self._wakeups = {w.wakeup_id: w.due_at for w in outcome.wakeups}
        if outcome.proactive is not None:
            self._proactive = outcome.proactive
        match outcome.status:
            case TurnStatus.SUSPENDED:
                self._suspended |= set(outcome.suspended)
                self._questions |= set(outcome.questions)
            case TurnStatus.INTERRUPTED:
                self._notices.append(
                    Notice(
                        kind=NoticeKind.INTERRUPTED,
                        data={
                            "superseded_actions": [str(a) for a in outcome.superseded],
                            "partial_response": outcome.partial,
                        },
                    )
                )
            case TurnStatus.STOPPED:
                view = await self._load()
                self._suspended |= {action.action_id for action in view.pending}
            case TurnStatus.LIMITED if outcome.limit is TurnLimit.BUDGET:
                self._paused = True
                self._pause_reason = PauseReason.BUDGET
            case TurnStatus.LIMITED:
                limit = outcome.limit.value if outcome.limit else None
                self._carry.append(Notice(kind=NoticeKind.LIMIT_REACHED, data={"limit": limit}))
            case TurnStatus.FAILED if retry:
                await self._wait(self._commands, MODEL_RETRY_DELAY)
                self._notices.append(
                    Notice(
                        kind=NoticeKind.RECOVERY,
                        data={"reason": "model_error", "error": outcome.reason},
                    )
                )
            case _:
                pass

    async def _wait_decisions(self) -> None:
        """Wait until every suspended action is decided, then run the approved ones."""
        await self._project(ChatStatus.NEEDS_APPROVAL)
        superseding = workflow.patched("supersede-approvals")
        while not self._commands():
            self._decided = False
            undecided = _undecided(await self._read(self._suspended))
            if not undecided:
                break
            if superseding and self._input:
                await workflow.execute_activity_method(
                    ChatActivities.supersede_actions,
                    [state.action_id for state in undecided],
                    start_to_close_timeout=_SHORT,
                )
                break
            deadline = min(s.created_at for s in undecided) + self._view.approval_ttl
            if workflow.now() >= deadline:
                await self._expire()
                continue
            wait = min(deadline - workflow.now(), RECONCILE_INTERVAL)
            await self._wait(
                lambda: self._decided or self._commands() or (superseding and self._input), wait
            )
        if self._commands():
            return
        results: list[ApprovedRun] = []
        for state in await self._read(self._suspended):
            if self._commands():
                return
            if state.status is ActionStatus.APPROVED:
                results.append(await self._run_approved(state))
            else:
                results.append(_result_of(state))
        self._suspended.clear()
        self._notices.append(
            Notice(
                kind=NoticeKind.APPROVAL_RESULT,
                data={"actions": [run.model_dump(mode="json") for run in results]},
            )
        )

    async def _run_approved(self, state: ActionState) -> ApprovedRun:
        attempts = TURN_ATTEMPTS if state.risk is Risk.READ else 1
        try:
            while True:
                queue, start = self._placement()
                try:
                    return await workflow.execute_activity_method(
                        ChatActivities.run_approved_action,
                        state.action_id,
                        task_queue=queue,
                        schedule_to_start_timeout=start,
                        start_to_close_timeout=_APPROVED_RUN,
                        retry_policy=RetryPolicy(maximum_attempts=attempts),
                    )
                except ActivityError as error:
                    if queue is None or not _host_lost(error):
                        raise
                    await self._lose_workspace()
        except ActivityError:
            await workflow.execute_activity_method(
                ChatActivities.cancel_actions,
                CancelActions(chat_id=self._chat_id, action_ids=[state.action_id]),
                start_to_close_timeout=_SHORT,
            )
            [current] = await self._read({state.action_id})
            return _result_of(current)

    async def _wait_answers(self) -> None:
        """Wait while a suspended turn needs the operator's answer; any input resumes."""
        await self._project(ChatStatus.NEEDS_INPUT)
        if await self._wait(
            lambda: self._input or bool(self._notices) or self._commands(), RECONCILE_INTERVAL
        ):
            return
        view = await self._load()
        self._questions = set(view.questions)
        self._input = self._input or view.has_input

    async def _rewind(self, edit: EditMessage) -> None:
        """Rewind the history to the checkpoint before the edited message and queue it."""
        result = await workflow.execute_activity_method(
            ChatActivities.rewind_history,
            RewindRequest(chat_id=self._chat_id, edit=edit),
            start_to_close_timeout=_SHORT,
        )
        if result.error is None:
            if result.notice:
                self._suspended.clear()
                self._questions.clear()
                self._notices = [Notice(kind=NoticeKind.HISTORY_REWOUND, data=result.notice)]
            self._input = True
            self._model_retry = False
        self._rewound[self._edits] = result
        self._edit = None

    async def _admit_events(self) -> None:
        """Start a turn for reported events unless the Proactive limits defer them."""
        events, self._events = self._events, []
        admission = await workflow.execute_activity_method(
            ChatActivities.admit_events,
            AdmitEvents(chat_id=self._chat_id, events=events),
            start_to_close_timeout=_SHORT,
        )
        if admission.allowed:
            self._notices += admission.events
            self._trigger = TurnTrigger.EVENT
        else:
            self._carry += admission.events

    def _next_wakeup(self) -> datetime | None:
        return min(self._wakeups.values()) if self._proactive and self._wakeups else None

    def _wakeup_due(self) -> bool:
        due = self._next_wakeup()
        return due is not None and workflow.now() >= due

    async def _fire_wakeups(self) -> None:
        """Start a turn for the due wakeups; a limit postpones them."""
        now = workflow.now()
        for wakeup_id, due_at in sorted(self._wakeups.items(), key=lambda item: item[1]):
            if due_at > now:
                break
            fired = await workflow.execute_activity_method(
                ChatActivities.fire_wakeup, wakeup_id, start_to_close_timeout=_SHORT
            )
            if fired.retry_at is not None:
                self._wakeups[wakeup_id] = fired.retry_at
                continue
            del self._wakeups[wakeup_id]
            if fired.notice is not None:
                self._notices.append(Notice(kind=NoticeKind.WAKEUP, data=fired.notice))
                self._trigger = TurnTrigger.WAKEUP

    async def _wait_paused(self) -> None:
        await self._project(ChatStatus.PAUSED, self._pause_reason)
        wait = RECONCILE_INTERVAL
        undecided = _undecided(await self._read(self._suspended)) if self._suspended else []
        if undecided:
            deadline = min(s.created_at for s in undecided) + self._view.approval_ttl
            if workflow.now() >= deadline:
                await self._expire()
                return
            wait = min(deadline - workflow.now(), wait)
        # `_commands()` is true for the whole pause, so it cannot be the wake condition.
        await self._wait(
            lambda: (
                not self._paused
                or self._stop
                or self._deleting is not None
                or self._edit is not None
            ),
            wait,
        )

    async def _wait_ready(self) -> bool:
        """Wait for input; return ``False`` when the chat stayed idle for ``chat.idle_ttl``."""
        await self._project(ChatStatus.READY)
        if (
            workflow.info().is_continue_as_new_suggested()
            and workflow.all_handlers_finished()
            and not self._carry
            and not self._workspace_requested()
        ):
            workflow.continue_as_new(self._chat_id)
        idle_until = workflow.now() + self._view.idle_ttl
        wakeups = workflow.patched("proactive-wakeups")
        while workflow.now() < idle_until or (wakeups and self._next_wakeup() is not None):
            wait = RECONCILE_INTERVAL
            if workflow.now() < idle_until:
                wait = min(idle_until - workflow.now(), wait)
            due = self._next_wakeup() if wakeups else None
            if due is not None:
                wait = min(wait, max(due - workflow.now(), timedelta(0)))
            if await self._wait(
                lambda: (
                    self._input
                    or bool(self._notices)
                    or bool(self._events)
                    or self._commands()
                    or (wakeups and self._wakeup_due())
                ),
                wait,
            ):
                if wakeups and self._wakeup_due() and not (self._input or self._commands()):
                    await self._fire_wakeups()
                    if not (self._notices or self._events):
                        continue
                return True
            if (await self._load()).has_input:
                self._input = True
                return True
        return False

    async def _stop_chat(self) -> None:
        await self._cancel_actions()
        self._notices.clear()
        self._questions.clear()
        self._events.clear()
        self._input = False
        self._model_retry = False
        self._stop = False

    async def _delete_chat(self, request: DeleteChat) -> None:
        """Stop everything of the chat and mark it deleted; its data stays until retention."""
        await self._cancel_actions()
        await workflow.execute_activity_method(
            WorkspaceActivities.cancel_executions, self._chat_id, start_to_close_timeout=_SHORT
        )
        self._paused = False
        self._pause_reason = None
        self._wakeups.clear()
        await self._project(ChatStatus.READY, deleted_by=request.deleted_by)
        self._deleted = True
        await self._hibernate_workspace(None)

    async def _cancel_actions(self) -> None:
        await workflow.execute_activity_method(
            ChatActivities.cancel_actions,
            CancelActions(chat_id=self._chat_id),
            start_to_close_timeout=_SHORT,
        )
        self._suspended.clear()

    async def _expire(self) -> None:
        await workflow.execute_activity_method(
            ChatActivities.expire_actions,
            ExpireActions(chat_id=self._chat_id, ttl=self._view.approval_ttl),
            start_to_close_timeout=_SHORT,
        )

    async def _load(self) -> ChatView:
        return await workflow.execute_activity_method(
            ChatActivities.load_chat, self._chat_id, start_to_close_timeout=_SHORT
        )

    async def _read(self, action_ids: set[UUID]) -> list[ActionState]:
        return await workflow.execute_activity_method(
            ChatActivities.read_actions,
            ReadActions(action_ids=sorted(action_ids)),
            start_to_close_timeout=_SHORT,
        )

    async def _project(
        self,
        status: ChatStatus,
        pause_reason: PauseReason | None = None,
        *,
        deleted_by: UUID | None = None,
    ) -> None:
        if self._status is status and self._pause_reason is pause_reason and deleted_by is None:
            return
        now = int(workflow.now().timestamp() * 1_000_000)
        self._version = max(self._version + 1, now)
        await workflow.execute_activity_method(
            ChatActivities.project_chat,
            Projection(
                chat_id=self._chat_id,
                status=status,
                version=self._version,
                pause_reason=pause_reason,
                deleted=deleted_by is not None,
                deleted_by=deleted_by,
            ),
            start_to_close_timeout=_SHORT,
        )
        self._status = status
        self._pause_reason = pause_reason

    async def _wait(self, condition: Callable[[], bool], within: timedelta) -> bool:
        """Wait for ``condition`` at most ``within``; return whether it holds.

        Workspace requests and the idle timer are served while waiting.
        """
        end = workflow.now() + within
        while True:
            await self._serve_workspace()
            if condition():
                return True
            now = workflow.now()
            if now >= end:
                return False
            step = end - now
            deadline = self._workspace_deadline()
            if deadline is not None and deadline > now:
                step = min(step, deadline - now)
            with contextlib.suppress(TimeoutError):
                await workflow.wait_condition(
                    lambda: condition() or self._workspace_requested(), timeout=step
                )


def _undecided(states: list[ActionState]) -> list[ActionState]:
    return [state for state in states if state.status is ActionStatus.SUSPENDED]


def _result_of(state: ActionState) -> ApprovedRun:
    return ApprovedRun(action_id=state.action_id, tool=state.tool, status=state.status, result="")


def _host_lost(error: ActivityError) -> bool:
    """Whether a host-bound activity was never picked up: its worker is gone."""
    cause = error.cause
    return isinstance(cause, ActivityTimeoutError) and cause.type is TimeoutType.SCHEDULE_TO_START


@workflow.defn(name="ExecutionWorkflow")
class ExecutionWorkflow:
    """One Execute run; retried only while the code has not started."""

    @workflow.run
    async def run(self, start: ExecutionStart) -> ExecutionDone:
        """Run the execution, report it to its chat and return its final status."""
        done = await self._run(start)
        if workflow.patched("job-finished"):
            await workflow.execute_activity_method(
                WorkspaceActivities.report_execution,
                start.execution_id,
                start_to_close_timeout=_SHORT,
            )
        return done

    async def _run(self, start: ExecutionStart) -> ExecutionDone:
        try:
            return await workflow.execute_activity_method(
                WorkspaceActivities.run_execution,
                start.execution_id,
                task_queue=start.task_queue,
                schedule_to_start_timeout=HOST_SCHEDULE_TO_START if start.host_bound else None,
                start_to_close_timeout=start.max_duration + _EXECUTION_MARGIN,
                heartbeat_timeout=timedelta(seconds=30),
                retry_policy=RetryPolicy(
                    maximum_attempts=EXECUTION_ATTEMPTS,
                    non_retryable_error_types=list(WORKSPACE_ERRORS),
                ),
                cancellation_type=workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
            )
        except (ActivityError, asyncio.CancelledError) as error:
            cancelled = isinstance(error, asyncio.CancelledError) or isinstance(
                error.cause, CancelledError
            )
            reason = "cancelled" if cancelled else str(error.cause or error)
            return await workflow.execute_activity_method(
                WorkspaceActivities.settle_execution,
                SettleExecution(
                    execution_id=start.execution_id, reason=reason, cancelled=cancelled
                ),
                start_to_close_timeout=_SHORT,
            )


@workflow.defn(name="MaintenanceWorkflow")
class MaintenanceWorkflow:
    """Periodic cleanup: orphaned containers and volumes, stuck input, retention, stale sources."""

    @workflow.run
    async def run(self) -> None:
        """Run one maintenance pass."""
        hosts = await workflow.execute_activity_method(
            MaintenanceActivities.live_workers, start_to_close_timeout=_SHORT
        )
        for host in hosts:
            try:
                await workflow.execute_activity_method(
                    WorkspaceActivities.workspace_cleanup,
                    task_queue=host_queue(workflow.info().task_queue, host),
                    schedule_to_start_timeout=HOST_SCHEDULE_TO_START,
                    start_to_close_timeout=_MAINTENANCE_OP,
                    retry_policy=RetryPolicy(maximum_attempts=1),
                )
            except ActivityError as error:
                workflow.logger.warning("cleanup on %s failed: %s", host, error.cause or error)
        await workflow.execute_activity_method(
            MaintenanceActivities.requeue_chats, start_to_close_timeout=_MAINTENANCE_OP
        )
        await workflow.execute_activity_method(
            MaintenanceActivities.apply_retention, start_to_close_timeout=_MAINTENANCE_OP
        )
        stale = await workflow.execute_activity_method(
            ObserveActivities.observe_stale, start_to_close_timeout=_MAINTENANCE_OP
        )
        await _deliver_alerts(stale)


@workflow.defn(name="ObservePollWorkflow")
class ObservePollWorkflow:
    """One poll of an Observe source: its URL, then child resources level by level."""

    @workflow.run
    async def run(self, source_id: UUID) -> None:
        """Poll the source unless it is no longer published."""
        start = await workflow.execute_activity_method(
            ObserveActivities.observe_start, source_id, start_to_close_timeout=_SHORT
        )
        if start is None:
            return
        seen = {start.root.key}
        pending = [start.root]
        alerts: list[UUID] = []
        complete, capped, auth_failed, failed = True, False, False, 0
        while pending and not auth_failed:
            batch, pending = pending[: start.concurrency], pending[start.concurrency :]
            for outcome in await asyncio.gather(*(self._poll(source_id, t) for t in batch)):
                alerts += outcome.alert_ids
                complete = complete and outcome.complete
                failed += not outcome.fetched
                auth_failed = auth_failed or outcome.auth_failed
                for child in outcome.children:
                    if child.key in seen:
                        continue
                    if len(seen) >= MAX_POLL_RESOURCES:
                        complete, capped = False, True
                        break
                    seen.add(child.key)
                    pending.append(child)
        if auth_failed:
            alerts += await workflow.execute_activity_method(
                ObserveActivities.observe_auth_lost, source_id, start_to_close_timeout=_SHORT
            )
        else:
            end = PollEnd(
                source_id=source_id,
                credential_id=start.credential_id,
                seen=sorted(seen),
                complete=complete,
                capped=capped,
                error=f"{failed} resources failed" if failed else None,
            )
            alerts += await workflow.execute_activity_method(
                ObserveActivities.observe_finish, end, start_to_close_timeout=_SHORT
            )
        await _deliver_alerts(alerts)

    async def _poll(self, source_id: UUID, task: ResourceTask) -> ResourceOutcome:
        try:
            return await workflow.execute_activity_method(
                ObserveActivities.observe_poll,
                ObserveStep(source_id=source_id, task=task),
                start_to_close_timeout=_OBSERVE_STEP,
                retry_policy=RetryPolicy(maximum_attempts=3),
            )
        except ActivityError as error:
            workflow.logger.warning("polling %s failed: %s", task.url, error.cause or error)
            return ResourceOutcome(fetched=False, complete=False)


async def _deliver_alerts(alert_ids: list[UUID]) -> None:
    """Report new alerts to their chats and deliver them to webhooks; failures are logged."""
    if not alert_ids:
        return
    if workflow.patched("chat-alerts"):
        try:
            await workflow.execute_activity_method(
                ObserveActivities.report_alerts, alert_ids, start_to_close_timeout=_SHORT
            )
        except ActivityError as error:
            workflow.logger.warning("alerts were not reported to chats: %s", error.cause or error)
    deliveries = await workflow.execute_activity_method(
        ObserveActivities.alert_deliveries, alert_ids, start_to_close_timeout=_SHORT
    )
    results = await asyncio.gather(
        *(
            workflow.execute_activity_method(
                ObserveActivities.deliver_webhook,
                delivery,
                start_to_close_timeout=_SHORT,
                retry_policy=_WEBHOOK_RETRY,
            )
            for delivery in deliveries
        ),
        return_exceptions=True,
    )
    for delivery, result in zip(deliveries, results, strict=True):
        if isinstance(result, BaseException):
            workflow.logger.warning(
                "alert %s was not delivered to subscription %s: %s",
                delivery.alert_id,
                delivery.subscription_id,
                result,
            )


@workflow.defn(name="ChatScheduleWorkflow")
class ChatScheduleWorkflow:
    """One run of a chat schedule: a message into a new or a pinned chat."""

    @workflow.run
    async def run(self, run: ChatScheduleRun) -> None:
        """Post the message; a new chat gets an id from this run."""
        message = ScheduledMessage(
            chat_id=run.chat_id or _uuid7(),
            message_id=_uuid7(),
            text=run.text,
            template=None if run.chat_id else run.template,
            created_by=run.created_by,
        )
        await workflow.execute_activity_method(
            ScheduleActivities.post_scheduled_message,
            message,
            start_to_close_timeout=_SHORT,
            retry_policy=RetryPolicy(maximum_attempts=5),
        )
