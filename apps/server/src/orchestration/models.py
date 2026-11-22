"""Inputs and outputs of workflows and activities."""

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)

from src.agent.notices import Notice
from src.db.models import (
    ActionStatus,
    ChatStatus,
    ExecutionStatus,
    PauseReason,
    TurnStatus,
    TurnTrigger,
    WorkspaceStatus,
)
from src.observe.service import ResourceTask
from src.observe.spec import Schedule
from src.policy.model import Mode, Risk


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True)


def host_queue(task_queue: str, host: str) -> str:
    """Return the task queue of activities bound to worker ``host``."""
    return f"{task_queue}.host.{host}"


class TurnInput(_Model):
    """One turn of a chat; the runner reads the chat and its queued messages from PostgreSQL."""

    chat_id: UUID
    turn_id: UUID
    notices: list[Notice] = []
    attempt_offset: int = 0
    """Added to the activity attempt; a turn moved to another queue keeps fencing earlier runs."""
    trigger: TurnTrigger = TurnTrigger.INPUT


class TurnLimit(StrEnum):
    """Which budget ended a ``limited`` turn."""

    STEPS = "steps"
    TIME = "time"
    BUDGET = "budget"
    TURN_BUDGET = "turn_budget"
    """The spending cap of a turn the agent started itself in Proactive mode."""


class WakeupView(_Model):
    """A pending wakeup the workflow times."""

    wakeup_id: UUID
    due_at: datetime


class TurnOutcome(_Model):
    """How a turn ended."""

    status: TurnStatus
    reason: str | None = None
    """Harness end reason of a ``failed`` turn."""
    limit: TurnLimit | None = None
    suspended: list[UUID] = []
    """Actions waiting for a decision when the turn is ``suspended``."""
    questions: list[UUID] = []
    """Questions the turn suspended on until the operator answers."""
    superseded: list[UUID] = []
    """Actions an ``interrupted`` turn cancelled before their decision."""
    partial: bool = False
    """An ``interrupted`` turn cut off a model response."""
    pending_input: bool = False
    """Messages were queued when the turn ended, such as late answers."""
    proactive: bool | None = None
    """Proactive setting when the turn ended; ``None`` when the runner did not report it."""
    wakeups: list[WakeupView] | None = None
    """Pending wakeups of the chat when the turn ended."""


class EditMessage(_Model):
    """A revision of an operator message, or a new response to it.

    The history is rewound to the checkpoint before the message; later turns are superseded.
    """

    message_id: UUID
    text: str | None = None
    """``None`` keeps the text: the agent answers the same message again."""
    image_artifact_ids: list[UUID] | None = None
    file_artifact_ids: list[UUID] | None = None


class RewindRequest(_Model):
    """Rewind the chat's history for an edit."""

    chat_id: UUID
    edit: EditMessage


class Fired(_Model):
    """Outcome of a due wakeup."""

    notice: dict[str, JsonValue] | None = None
    """Data of the ``wakeup`` notice; ``None`` when the wakeup no longer applies."""
    retry_at: datetime | None = None
    """A Proactive limit postpones the wakeup to this time."""


class Rewound(_Model):
    """Result of a rewind."""

    message_id: UUID | None = None
    """The queued revision."""
    error: str | None = None
    """Why the history cannot be rewound to the message."""
    notice: dict[str, JsonValue] = {}


class AdmitEvents(_Model):
    """Events reported to a chat, to start a proactive turn for."""

    chat_id: UUID
    events: list[Notice]


class Admission(_Model):
    """Whether a proactive turn may start now."""

    allowed: bool
    retry_at: datetime | None = None
    reason: str | None = None
    events: list[Notice] = []
    """The admitted events that still apply."""


class SettleTurn(_Model):
    """Close a turn whose activity did not store an outcome."""

    turn_id: UUID
    status: TurnStatus


class ActionState(_Model):
    """Current state of an action."""

    action_id: UUID
    tool: str
    status: ActionStatus
    risk: Risk
    created_at: datetime


class WorkspaceView(_Model):
    """Workspace state the chat workflow routes and times by."""

    status: WorkspaceStatus | None = None
    """``None`` when the chat never used a workspace."""
    host: str | None = None
    epoch: int = 0
    touched_at: datetime | None = None


class ChatView(_Model):
    """What the workflow restores from PostgreSQL when it starts."""

    deleted: bool
    paused: bool
    pause_reason: PauseReason | None
    has_input: bool
    """Whether queued messages exist."""
    pending: list[ActionState]
    """Actions in ``suspended`` or ``approved``."""
    questions: list[UUID] = []
    """Unanswered questions a suspended turn waits for."""
    proactive: bool = False
    wakeups: list[WakeupView] = []
    """Pending wakeups, earliest first."""
    turn_timeout: timedelta
    approval_ttl: timedelta
    idle_ttl: timedelta
    workspace_idle_ttl: timedelta
    workspace: WorkspaceView


class Projection(_Model):
    """Chat status written to PostgreSQL."""

    chat_id: UUID
    status: ChatStatus
    version: int
    pause_reason: PauseReason | None = None
    deleted: bool = False
    """Mark the chat deleted unless it already is."""
    deleted_by: UUID | None = None


class DeleteChat(_Model):
    """Argument of the ``delete`` update."""

    deleted_by: UUID
    """The user who deleted the chat."""


class ReadActions(_Model):
    """Actions to read."""

    action_ids: list[UUID]


class ExpireActions(_Model):
    """Expire suspended actions of the chat older than ``ttl``."""

    chat_id: UUID
    ttl: timedelta


class CancelActions(_Model):
    """Cancel actions of the chat.

    Without ``action_ids`` every action waiting for a decision or execution is cancelled. Listed
    actions are also settled when ``running``: they end as ``uncertain``.
    """

    chat_id: UUID
    action_ids: list[UUID] | None = None


class ApprovedRun(_Model):
    """Result of running an action that was approved after its turn was suspended."""

    action_id: UUID
    tool: str
    status: ActionStatus
    result: str
    """The JSON text the model would have received."""


class CommandResult(_Model):
    """Chat state returned by workflow updates."""

    status: ChatStatus
    pause_reason: PauseReason | None = None


class Activation(_Model):
    """Result of activating a workspace."""

    workspace: WorkspaceView
    restored_at: datetime | None = None
    """Time of the checkpoint this activation restored."""


class HibernateWorkspace(_Model):
    """Hibernate the workspace, if idle for ``idle_ttl`` when set."""

    chat_id: UUID
    idle_ttl: timedelta | None = None


class LoseWorkspace(_Model):
    """Release a workspace whose host stopped taking tasks."""

    chat_id: UUID
    epoch: int


class ExecutionStart(_Model):
    """Input of `ExecutionWorkflow`."""

    execution_id: UUID
    task_queue: str
    """Queue of ``run_execution``: the workspace host for chat runs, shared for API runs."""
    host_bound: bool
    max_duration: timedelta


class SettleExecution(_Model):
    """Close an execution whose activity did not finish it."""

    execution_id: UUID
    reason: str
    cancelled: bool = False


class ExecutionDone(_Model):
    """Result of `ExecutionWorkflow`."""

    status: ExecutionStatus


class ChatTemplate(_Model):
    """The chat a schedule creates, with settings resolved when the schedule was stored."""

    title: str
    model: str
    reasoning: str
    mode: Mode
    tool_groups: list[str]
    execute_network: bool
    budget_usd: float
    max_steps: int | None = 50
    """Step limit of created chats. ``None`` sets no limit. Absent on an old payload means 50."""


class ChatTiming(Schedule):
    """When a chat schedule posts: once at `at`, or on an `interval` or `cron` timetable."""

    at: AwareDatetime | None = Field(
        default=None, description="Post once at this time, to the second; then the schedule ends."
    )

    @field_validator("at")
    @classmethod
    def _utc_second(cls, at: datetime | None) -> datetime | None:
        return at and at.astimezone(UTC).replace(microsecond=0)

    @model_validator(mode="after")
    def _one_kind(self) -> Self:
        if self.at is None and (self.interval is not None or self.cron is not None):
            return self.check_timetable()
        if self.at is None or self.interval is not None or self.cron is not None:
            raise ValueError("pass exactly one of `at`, `interval` and `cron`")
        return self


class ChatScheduleRun(_Model):
    """Input of `ChatScheduleWorkflow`: a message into a new chat or into a pinned one."""

    created_by: UUID
    """The owner; chats the schedule creates belong to them."""
    name: str
    timing: ChatTiming
    text: str
    chat_id: UUID | None
    """Pinned chat; ``None`` creates a chat from ``template`` on every run."""
    template: ChatTemplate | None
    """Settings of new chats; ``None`` with a pinned chat."""


class ScheduledMessage(_Model):
    """A scheduled message; the ids make retries idempotent."""

    chat_id: UUID
    message_id: UUID
    text: str
    template: ChatTemplate | None
    """Creates the chat if it does not exist."""
    created_by: UUID
    """Owner of the schedule; a pinned chat must still belong to them."""


class ObserveStep(_Model):
    """One resource of a poll."""

    source_id: UUID
    task: ResourceTask
