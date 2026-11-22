"""Activities of the chat, execution, Observe, schedule and maintenance workflows."""

from collections.abc import Awaitable, Callable
from datetime import timedelta
from uuid import UUID

import mcp_types as mt
from sqlalchemy import delete, exists, func, select, update
from sqlalchemy.dialects.postgresql import insert
from temporalio import activity
from temporalio.client import Client
from temporalio.exceptions import ApplicationError

from src.agent import history
from src.agent.gateway import SUPERSEDED, Actions, Gateway, StaleAttemptError, chat_scope
from src.agent.notices import Notice, NoticeKind, current
from src.agent.proactive import admission, wakeup_event
from src.agent.proactive import cancel as cancel_wakeups
from src.agent.runner import Runner
from src.config import Settings
from src.db.engine import Sessions
from src.db.models import (
    FINAL_EXECUTION_STATUSES,
    PENDING_ACTION_STATUSES,
    Action,
    ActionStatus,
    Alert,
    Artifact,
    Chat,
    ChatStatus,
    Execution,
    ExecutionStatus,
    Message,
    MessageStatus,
    Question,
    QuestionStatus,
    Source,
    Turn,
    TurnStatus,
    Wakeup,
    WakeupStatus,
    Worker,
    Workspace,
    WorkspaceStatus,
)
from src.errors import NotFoundError
from src.events import NewEvent, append_events
from src.execute.service import ExecuteRunner, Executions
from src.observe.service import Delivery, Observe, PollEnd, PollStart, ResourceOutcome
from src.orchestration.models import (
    ActionState,
    Activation,
    Admission,
    AdmitEvents,
    ApprovedRun,
    CancelActions,
    ChatView,
    ExecutionDone,
    ExpireActions,
    Fired,
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
    TurnOutcome,
    WakeupView,
    WorkspaceView,
)
from src.orchestration.schedules import SourceSchedules
from src.storage.artifacts import artifact_key
from src.storage.blobs import BlobStore, delete_prefix
from src.telemetry import TURN_RETRIES
from src.workspace.service import WorkspaceError, WorkspaceService, mark_lost

STALE_ATTEMPT = "stale_attempt"
CHAT_NOT_FOUND = "chat_not_found"
WORKSPACE_ERRORS = ("host_full", "env_busy", "workspace_full")
"""Activation failures that a retry on the same queue does not fix."""
WORKER_ALIVE = timedelta(minutes=1)
"""A worker whose heartbeat is older is not asked to clean up."""
QUEUED_TOO_LONG = timedelta(minutes=2)
ALERT_NOTICE_CHARS = 1000

_RESTORED = (ActionStatus.SUSPENDED, ActionStatus.APPROVED)
_UNFINISHED = (ExecutionStatus.PENDING, ExecutionStatus.RUNNING)


class ChatActivities:
    """Activities that read and write chat state, run turns and approved actions."""

    def __init__(
        self,
        settings: Settings,
        sessions: Sessions,
        runner: Runner,
        gateway: Gateway,
        blobs: BlobStore,
    ) -> None:
        self._settings = settings
        self._sessions = sessions
        self._runner = runner
        self._gateway = gateway
        self._blobs = blobs
        self._actions = Actions(sessions)

    @activity.defn(name="ensure_chat")
    async def ensure_chat(self, chat_id: UUID) -> None:
        """Fail without retries if the chat does not exist."""
        async with self._sessions() as session:
            if await session.get(Chat, chat_id) is None:
                raise ApplicationError(
                    f"chat {chat_id} not found", type=CHAT_NOT_FOUND, non_retryable=True
                )

    @activity.defn(name="load_chat")
    async def load_chat(self, chat_id: UUID) -> ChatView:
        """Return the chat state the workflow restores."""
        async with self._sessions() as session:
            chat = await session.get_one(Chat, chat_id)
            has_input = await session.scalar(
                select(
                    exists().where(
                        Message.chat_id == chat_id, Message.status == MessageStatus.QUEUED
                    )
                )
            )
            pending = (
                await session.scalars(
                    select(Action)
                    .where(Action.chat_id == chat_id, Action.status.in_(_RESTORED))
                    .order_by(Action.id)
                )
            ).all()
            questions = (
                await session.scalars(
                    select(Question.id).where(
                        Question.chat_id == chat_id,
                        Question.status == QuestionStatus.PENDING,
                        Question.waiting.is_(True),
                        Question.created_at > func.now() - self._settings.approval.ttl,
                    )
                )
            ).all()
            wakeups = (
                await session.execute(
                    select(Wakeup.id, Wakeup.due_at)
                    .where(Wakeup.chat_id == chat_id, Wakeup.status == WakeupStatus.PENDING)
                    .order_by(Wakeup.due_at)
                )
            ).all()
        return ChatView(
            deleted=chat.deleted_at is not None,
            paused=chat.status is ChatStatus.PAUSED,
            pause_reason=chat.pause_reason,
            has_input=bool(has_input),
            pending=[_state(action) for action in pending],
            questions=list(questions),
            proactive=chat.proactive,
            wakeups=[WakeupView(wakeup_id=w_id, due_at=due) for w_id, due in wakeups],
            turn_timeout=self._settings.turn.timeout,
            approval_ttl=self._settings.approval.ttl,
            idle_ttl=self._settings.chat.idle_ttl,
            workspace_idle_ttl=self._settings.workspace.idle_ttl,
            workspace=await workspace_view(self._sessions, chat_id),
        )

    @activity.defn(name="project_chat")
    async def project_chat(self, projection: Projection) -> None:
        """Write the chat status unless a newer projection is stored; a deletion is kept."""
        deleted = (
            {
                "deleted_at": func.coalesce(Chat.deleted_at, func.now()),
                "deleted_by": func.coalesce(Chat.deleted_by, projection.deleted_by),
            }
            if projection.deleted
            else {}
        )
        async with self._sessions.begin() as session:
            row = await session.scalar(
                update(Chat)
                .where(Chat.id == projection.chat_id, Chat.status_version < projection.version)
                .values(
                    status=projection.status,
                    status_version=projection.version,
                    pause_reason=projection.pause_reason,
                    **deleted,
                )
                .returning(Chat.id)
            )
            if projection.deleted:
                await cancel_wakeups(session, projection.chat_id)
            if row is not None:
                await append_events(
                    session,
                    projection.chat_id,
                    [
                        NewEvent(
                            "chat",
                            {
                                "status": projection.status.value,
                                "pause_reason": projection.pause_reason,
                            },
                        )
                    ],
                )

    @activity.defn(name="run_turn")
    async def run_turn(self, turn: TurnInput) -> TurnOutcome:
        """Run one agent turn."""
        try:
            attempt = turn.attempt_offset + activity.info().attempt
            if activity.info().attempt > 1:
                TURN_RETRIES.add(1)
            return await self._runner.run(turn, attempt, activity.heartbeat)
        except StaleAttemptError as error:
            raise ApplicationError(str(error), type=STALE_ATTEMPT, non_retryable=True) from error

    @activity.defn(name="settle_turn")
    async def settle_turn(self, settle: SettleTurn) -> None:
        """Store the outcome of a turn left ``running``."""
        async with self._sessions.begin() as session:
            row = await session.scalar(
                update(Turn)
                .where(Turn.id == settle.turn_id, Turn.status == TurnStatus.RUNNING)
                .values(status=settle.status, finished_at=func.now())
                .returning(Turn.chat_id)
            )
            if row is not None:
                await append_events(
                    session,
                    row,
                    [NewEvent("turn", {"status": settle.status.value}, settle.turn_id)],
                )

    @activity.defn(name="run_approved_action")
    async def run_approved_action(self, action_id: UUID) -> ApprovedRun:
        """Execute an action approved while its turn was suspended."""
        action = await self._actions.get(action_id)
        async with self._sessions() as session:
            chat = await session.get_one(Chat, action.chat_id)
        model = self._settings.agent.model(chat.model)
        scope = chat_scope(
            chat,
            turn_id=action.turn_id,
            attempt=action.attempt,
            image_input="image" in model.input,
        )
        result = await self._gateway.run_approved(action_id, scope)
        return ApprovedRun(
            action_id=action_id,
            tool=action.tool,
            status=await self._actions.status(action_id),
            result=_text(result) if result is not None else "",
        )

    @activity.defn(name="read_actions")
    async def read_actions(self, request: ReadActions) -> list[ActionState]:
        """Return the current state of the actions."""
        async with self._sessions() as session:
            actions = await session.scalars(
                select(Action).where(Action.id.in_(request.action_ids)).order_by(Action.id)
            )
            return [_state(action) for action in actions]

    @activity.defn(name="expire_actions")
    async def expire_actions(self, request: ExpireActions) -> None:
        """Expire suspended actions that waited longer than the approval TTL."""
        async with self._sessions() as session:
            expired = (
                await session.scalars(
                    select(Action.id).where(
                        Action.chat_id == request.chat_id,
                        Action.status == ActionStatus.SUSPENDED,
                        Action.created_at <= func.now() - request.ttl,
                    )
                )
            ).all()
        for action_id in expired:
            await self._actions.transition(
                action_id, {ActionStatus.SUSPENDED}, ActionStatus.EXPIRED, error_code="expired"
            )

    @activity.defn(name="cancel_actions")
    async def cancel_actions(self, request: CancelActions) -> None:
        """Cancel actions that wait for a decision or execution."""
        waiting = {*PENDING_ACTION_STATUSES, ActionStatus.APPROVED}
        query = select(Action.id, Action.status).where(Action.chat_id == request.chat_id)
        if request.action_ids is None:
            query = query.where(Action.status.in_(waiting))
        else:
            query = query.where(Action.id.in_(request.action_ids))
        async with self._sessions() as session:
            rows = (await session.execute(query)).all()
        for action_id, status in rows:
            if status in waiting:
                await self._actions.transition(
                    action_id, waiting, ActionStatus.CANCELLED, error_code="cancelled"
                )
            elif status is ActionStatus.RUNNING:
                await self._actions.transition(
                    action_id, {status}, ActionStatus.UNCERTAIN, error_code="interrupted"
                )

    @activity.defn(name="supersede_actions")
    async def supersede_actions(self, action_ids: list[UUID]) -> None:
        """Cancel undecided actions that new operator input replaced."""
        await self._actions.supersede(action_ids)

    @activity.defn(name="rewind_history")
    async def rewind_history(self, request: RewindRequest) -> Rewound:
        """Supersede the turns from the edited message on and queue its revision."""
        rewound = await history.rewind(self._sessions, self._blobs, request)
        for action_id in rewound.cancelled:
            await self._actions.transition(
                action_id,
                {*PENDING_ACTION_STATUSES, ActionStatus.APPROVED},
                ActionStatus.CANCELLED,
                error_code=SUPERSEDED,
                error="the history was rewound",
            )
        return rewound.result

    @activity.defn(name="admit_events")
    async def admit_events(self, request: AdmitEvents) -> Admission:
        """Return the events that still apply and whether they may start a turn now."""
        async with self._sessions() as session:
            events = await current(session, request.events)
            if not events:
                return Admission(allowed=False, reason="the events no longer apply")
            admitted = await admission(session, request.chat_id, self._settings.proactive)
        return admitted.model_copy(update={"events": events})

    @activity.defn(name="fire_wakeup")
    async def fire_wakeup(self, wakeup_id: UUID) -> Fired:
        """Fire a due wakeup if it still applies and fits the Proactive limits."""
        async with self._sessions.begin() as session:
            wakeup = await session.get(Wakeup, wakeup_id, with_for_update=True)
            if wakeup is None or wakeup.status is not WakeupStatus.PENDING:
                return Fired()
            admitted = await admission(session, wakeup.chat_id, self._settings.proactive)
            if not admitted.allowed and admitted.retry_at is not None:
                return Fired(retry_at=admitted.retry_at)
            wakeup.status = WakeupStatus.FIRED if admitted.allowed else WakeupStatus.CANCELLED
            if admitted.allowed:
                wakeup.fired_at = func.now()
            await append_events(session, wakeup.chat_id, [wakeup_event(wakeup)])
            if not admitted.allowed:
                return Fired()
            return Fired(
                notice={
                    "wakeup_id": str(wakeup.id),
                    "reason": wakeup.reason,
                    "scheduled_for": wakeup.due_at.isoformat(),
                }
            )

    def all(self) -> list[Callable[..., Awaitable[object]]]:
        """Return the activity callables for worker registration."""
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
            self.supersede_actions,
            self.rewind_history,
            self.admit_events,
            self.fire_wakeup,
        ]


class WorkspaceActivities:
    """Workspace lifecycle and Execute runs; host-bound ones run on the workspace host queue."""

    def __init__(
        self,
        sessions: Sessions,
        workspaces: WorkspaceService,
        executions: Executions,
        runner: ExecuteRunner,
        task_queue: str,
    ) -> None:
        self._sessions = sessions
        self._workspaces = workspaces
        self._executions = executions
        self._runner = runner
        self._task_queue = task_queue

    @activity.defn(name="workspace_state")
    async def workspace_state(self, chat_id: UUID) -> WorkspaceView:
        """Return the workspace state."""
        return await workspace_view(self._sessions, chat_id)

    @activity.defn(name="workspace_activate")
    async def workspace_activate(self, chat_id: UUID) -> Activation:
        """Activate the workspace on this worker unless it is active."""
        try:
            active = await self._workspaces.activate(chat_id)
        except WorkspaceError as error:
            raise _workspace_failure(error) from error
        return Activation(
            workspace=await workspace_view(self._sessions, chat_id),
            restored_at=active.restored_at,
        )

    @activity.defn(name="workspace_checkpoint")
    async def workspace_checkpoint(self, chat_id: UUID) -> WorkspaceView:
        """Checkpoint the workspace if it changed."""
        await self._workspaces.checkpoint(chat_id)
        return await workspace_view(self._sessions, chat_id)

    @activity.defn(name="workspace_hibernate")
    async def workspace_hibernate(self, request: HibernateWorkspace) -> WorkspaceView:
        """Checkpoint and release the workspace unless it is in use."""
        await self._workspaces.hibernate(request.chat_id, request.idle_ttl)
        return await workspace_view(self._sessions, request.chat_id)

    @activity.defn(name="workspace_delete")
    async def workspace_delete(self, chat_id: UUID) -> WorkspaceView:
        """Delete the volumes on this worker and every checkpoint."""
        await self._workspaces.delete(chat_id)
        return await workspace_view(self._sessions, chat_id)

    @activity.defn(name="workspace_lost")
    async def workspace_lost(self, request: LoseWorkspace) -> WorkspaceView:
        """Release a workspace whose host stopped taking tasks; its last checkpoint is kept."""
        await mark_lost(self._sessions, request.chat_id, request.epoch)
        return await workspace_view(self._sessions, request.chat_id)

    @activity.defn(name="workspace_cleanup")
    async def workspace_cleanup(self) -> None:
        """Remove containers and volumes of this worker that nothing owns."""
        await self._workspaces.cleanup_orphans()

    @activity.defn(name="cancel_executions")
    async def cancel_executions(self, chat_id: UUID) -> None:
        """Cancel the chat's unfinished executions."""
        async with self._sessions() as session:
            ids = (
                await session.scalars(
                    select(Execution.id).where(
                        Execution.chat_id == chat_id, Execution.status.in_(_UNFINISHED)
                    )
                )
            ).all()
        for execution_id in ids:
            await self._executions.cancel(execution_id)

    @activity.defn(name="run_execution")
    async def run_execution(self, execution_id: UUID) -> ExecutionDone:
        """Run the execution once; see `ExecuteRunner.run` for the retry rules."""
        try:
            status = await self._runner.run(execution_id, activity.heartbeat)
        except WorkspaceError as error:
            raise _workspace_failure(error) from error
        return ExecutionDone(status=status)

    @activity.defn(name="settle_execution")
    async def settle_execution(self, request: SettleExecution) -> ExecutionDone:
        """Close an execution its run did not finish and return its status."""
        await self._executions.settle(
            request.execution_id, request.reason, cancelled=request.cancelled
        )
        return ExecutionDone(status=(await self._executions.get(request.execution_id)).status)

    @activity.defn(name="report_execution")
    async def report_execution(self, execution_id: UUID) -> None:
        """Tell the chat about a finished job whose results the agent has not read."""
        async with self._sessions() as session:
            execution = await session.get_one(Execution, execution_id)
            chat = await session.get(Chat, execution.chat_id) if execution.chat_id else None
        if (
            chat is None
            or chat.deleted_at is not None
            or execution.collected_at is not None
            or execution.status not in FINAL_EXECUTION_STATUSES
        ):
            return
        notice = Notice(
            kind=NoticeKind.JOB_FINISHED,
            data={
                "job_id": str(execution.id),
                "kind": execution.kind.value,
                "job_status": execution.status.value,
                "exit_code": execution.exit_code,
            },
        )
        await _chat_event(chat.id, notice, self._task_queue)

    def all(self) -> list[Callable[..., Awaitable[object]]]:
        """Return the activity callables for worker registration."""
        return [
            self.workspace_state,
            self.workspace_activate,
            self.workspace_checkpoint,
            self.workspace_hibernate,
            self.workspace_delete,
            self.workspace_lost,
            self.workspace_cleanup,
            self.cancel_executions,
            self.run_execution,
            self.settle_execution,
            self.report_execution,
        ]


class MaintenanceActivities:
    """Periodic recovery and retention."""

    def __init__(
        self, settings: Settings, sessions: Sessions, blobs: BlobStore, client: Client
    ) -> None:
        self._settings = settings
        self._sessions = sessions
        self._blobs = blobs
        self._client = client

    @activity.defn(name="live_workers")
    async def live_workers(self) -> list[str]:
        """Return the ids of workers with a recent heartbeat."""
        async with self._sessions() as session:
            rows = await session.scalars(
                select(Worker.id).where(Worker.heartbeat_at > func.now() - WORKER_ALIVE)
            )
            return sorted(rows)

    @activity.defn(name="requeue_chats")
    async def requeue_chats(self) -> int:
        """Signal chats whose messages stayed queued too long, starting their workflows."""
        async with self._sessions() as session:
            chat_ids = (
                await session.scalars(
                    select(Message.chat_id)
                    .join(Chat, Chat.id == Message.chat_id)
                    .where(
                        Message.status == MessageStatus.QUEUED,
                        Message.created_at < func.now() - QUEUED_TOO_LONG,
                        Chat.deleted_at.is_(None),
                    )
                    .distinct()
                )
            ).all()
        for chat_id in chat_ids:
            await self._client.start_workflow(
                "ChatWorkflow",
                chat_id,
                id=str(chat_id),
                task_queue=self._settings.temporal.task_queue,
                start_signal="input_available",
            )
        return len(chat_ids)

    @activity.defn(name="apply_retention")
    async def apply_retention(self) -> None:
        """Delete data past its retention period."""
        retention = self._settings.retention
        async with self._sessions.begin() as session:
            expired_checkpoints = (
                await session.scalars(
                    update(Workspace)
                    .where(
                        Workspace.status == WorkspaceStatus.HIBERNATED,
                        func.coalesce(Workspace.checkpoint_at, Workspace.touched_at)
                        < func.now() - retention.checkpoints,
                    )
                    .values(status=WorkspaceStatus.DELETED, checkpoint=None, checkpoint_at=None)
                    .returning(Workspace.chat_id)
                )
            ).all()
        for chat_id in expired_checkpoints:
            await delete_prefix(self._blobs, f"workspaces/{chat_id}/")
        async with self._sessions() as session:
            expired_chats = (
                await session.scalars(
                    select(Chat.id).where(Chat.deleted_at < func.now() - retention.deleted)
                )
            ).all()
        for chat_id in expired_chats:
            await self._delete_chat(chat_id)
        await self._delete_api_executions(retention.executions)

    async def _delete_chat(self, chat_id: UUID) -> None:
        async with self._sessions.begin() as session:
            artifact_ids = (
                await session.scalars(select(Artifact.id).where(Artifact.chat_id == chat_id))
            ).all()
            await session.execute(delete(Chat).where(Chat.id == chat_id))
        await self._blobs.delete([artifact_key(artifact_id) for artifact_id in artifact_ids])
        for prefix in (f"snapshots/{chat_id}/", f"workspaces/{chat_id}/"):
            await delete_prefix(self._blobs, prefix)

    async def _delete_api_executions(self, age: timedelta) -> None:
        async with self._sessions.begin() as session:
            outputs = (
                await session.scalars(
                    delete(Execution)
                    .where(Execution.chat_id.is_(None), Execution.finished_at < func.now() - age)
                    .returning(Execution.output_artifact_ids)
                )
            ).all()
            artifact_ids = [artifact_id for ids in outputs for artifact_id in ids]
            if artifact_ids:
                await session.execute(delete(Artifact).where(Artifact.id.in_(artifact_ids)))
        await self._blobs.delete([artifact_key(artifact_id) for artifact_id in artifact_ids])

    def all(self) -> list[Callable[..., Awaitable[object]]]:
        """Return the activity callables for worker registration."""
        return [self.live_workers, self.requeue_chats, self.apply_retention]


class ObserveActivities:
    """Polling of Observe sources and delivery of their alerts."""

    def __init__(
        self, sessions: Sessions, observe: Observe, schedules: SourceSchedules, task_queue: str
    ) -> None:
        self._sessions = sessions
        self._observe = observe
        self._schedules = schedules
        self._task_queue = task_queue

    @activity.defn(name="observe_start")
    async def observe_start(self, source_id: UUID) -> PollStart | None:
        """Return the first resource of a poll, or ``None`` when the source is not published."""
        return await self._observe.start(source_id)

    @activity.defn(name="observe_poll")
    async def observe_poll(self, step: ObserveStep) -> ResourceOutcome:
        """Fetch and observe one resource."""
        return await self._observe.poll(step.source_id, step.task)

    @activity.defn(name="observe_finish")
    async def observe_finish(self, end: PollEnd) -> list[UUID]:
        """Record the poll and the resources that are gone."""
        return await self._observe.finish(end)

    @activity.defn(name="observe_auth_lost")
    async def observe_auth_lost(self, source_id: UUID) -> list[UUID]:
        """Pause the sources of a rejected credential with their schedules."""
        lost = await self._observe.auth_lost(source_id)
        for paused in lost.paused:
            await self._schedules.pause(paused, "credential rejected")
        return lost.alert_ids

    @activity.defn(name="observe_stale")
    async def observe_stale(self) -> list[UUID]:
        """Raise ``stale`` for published sources that missed scheduled polls."""
        alerts: list[UUID] = []
        for source_id in await self._observe.published():
            try:
                runs = await self._schedules.recent_runs(source_id)
            except NotFoundError:
                continue
            if (alert := await self._observe.stale(source_id, runs)) is not None:
                alerts.append(alert)
        return alerts

    @activity.defn(name="alert_deliveries")
    async def alert_deliveries(self, alert_ids: list[UUID]) -> list[Delivery]:
        """Return the webhook deliveries of new alerts."""
        return await self._observe.deliveries(alert_ids)

    @activity.defn(name="deliver_webhook")
    async def deliver_webhook(self, delivery: Delivery) -> None:
        """POST one alert to one webhook; a failure is retried."""
        await self._observe.deliver(delivery)

    @activity.defn(name="report_alerts")
    async def report_alerts(self, alert_ids: list[UUID]) -> None:
        """Report new alerts to the chats whose agents drafted their sources."""
        async with self._sessions() as session:
            rows = await session.execute(
                select(Alert, Source.name, Chat.id)
                .join(Source, Source.id == Alert.source_id)
                .join(Chat, Chat.id == Source.chat_id)
                .where(Alert.id.in_(alert_ids), Chat.deleted_at.is_(None))
                .order_by(Alert.id)
            )
            reports = [
                (
                    chat_id,
                    Notice(
                        kind=NoticeKind.ALERT,
                        data={
                            "alert_id": str(alert.id),
                            "source_id": str(alert.source_id),
                            "source": name,
                            "kind": alert.kind.value,
                            "resource": alert.resource_key,
                            "message": alert.message[:ALERT_NOTICE_CHARS],
                        },
                    ),
                )
                for alert, name, chat_id in rows
            ]
        for chat_id, notice in reports:
            await _chat_event(chat_id, notice, self._task_queue)

    def all(self) -> list[Callable[..., Awaitable[object]]]:
        """Return the activity callables for worker registration."""
        return [
            self.observe_start,
            self.observe_poll,
            self.observe_finish,
            self.observe_auth_lost,
            self.observe_stale,
            self.alert_deliveries,
            self.deliver_webhook,
            self.report_alerts,
        ]


class ScheduleActivities:
    """Messages posted by chat schedules."""

    def __init__(self, sessions: Sessions, client: Client, task_queue: str) -> None:
        self._sessions = sessions
        self._client = client
        self._task_queue = task_queue

    @activity.defn(name="post_scheduled_message")
    async def post_scheduled_message(self, message: ScheduledMessage) -> None:
        """Create the chat if needed, queue the message and start the agent."""
        async with self._sessions.begin() as session:
            if (template := message.template) is not None:
                await session.execute(
                    insert(Chat)
                    .values(
                        id=message.chat_id,
                        created_by=message.created_by,
                        title=template.title,
                        status=ChatStatus.WORKING,
                        model=template.model,
                        reasoning=template.reasoning,
                        mode=template.mode,
                        tool_groups=template.tool_groups,
                        execute_network=template.execute_network,
                        budget_usd=template.budget_usd,
                        max_steps=template.max_steps,
                    )
                    .on_conflict_do_nothing()
                )
            chat = await session.get(Chat, message.chat_id)
            if chat is None:
                raise ApplicationError(
                    f"chat {message.chat_id} not found", type=CHAT_NOT_FOUND, non_retryable=True
                )
            if chat.created_by != message.created_by:
                raise ApplicationError(
                    f"chat {message.chat_id} not found", type=CHAT_NOT_FOUND, non_retryable=True
                )
            if chat.deleted_at is not None:
                raise ApplicationError(
                    f"chat {message.chat_id} is deleted", type="chat_deleted", non_retryable=True
                )
            await session.execute(
                insert(Message)
                .values(
                    id=message.message_id,
                    chat_id=message.chat_id,
                    text=message.text,
                    image_artifact_ids=[],
                    file_artifact_ids=[],
                    status=MessageStatus.QUEUED,
                )
                .on_conflict_do_nothing()
            )
        await self._client.start_workflow(
            "ChatWorkflow",
            message.chat_id,
            id=str(message.chat_id),
            task_queue=self._task_queue,
            start_signal="input_available",
        )

    def all(self) -> list[Callable[..., Awaitable[object]]]:
        """Return the activity callables for worker registration."""
        return [self.post_scheduled_message]


async def workspace_view(sessions: Sessions, chat_id: UUID) -> WorkspaceView:
    """Return the workspace state of the chat."""
    async with sessions() as session:
        row = await session.get(Workspace, chat_id)
    if row is None:
        return WorkspaceView()
    return WorkspaceView(
        status=row.status, host=row.host, epoch=row.epoch, touched_at=row.touched_at
    )


async def _chat_event(chat_id: UUID, notice: Notice, task_queue: str) -> None:
    """Report an event to the chat, starting its workflow if it is not running."""
    await activity.client().start_workflow(
        "ChatWorkflow",
        chat_id,
        id=str(chat_id),
        task_queue=task_queue,
        start_signal="chat_event",
        start_signal_args=[notice],
    )


def _workspace_failure(error: WorkspaceError) -> ApplicationError:
    return ApplicationError(error.message, type=error.code, non_retryable=True)


def _state(action: Action) -> ActionState:
    return ActionState(
        action_id=action.id,
        tool=action.tool,
        status=action.status,
        risk=action.risk,
        created_at=action.created_at,
    )


def _text(result: mt.CallToolResult) -> str:
    return "".join(block.text for block in result.content if isinstance(block, mt.TextContent))
