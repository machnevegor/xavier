"""ORM tables and the stored state vocabulary."""

import enum
import uuid
from datetime import datetime
from enum import StrEnum

from pydantic import JsonValue
from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    MetaData,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from src.policy.model import Effect, Mode, Risk


class ChatStatus(StrEnum):
    """Chat state projected from `ChatWorkflow`."""

    WORKING = "working"
    NEEDS_APPROVAL = "needs_approval"
    NEEDS_INPUT = "needs_input"
    """The agent waits for the operator to answer a question."""
    READY = "ready"
    PAUSED = "paused"


class PauseReason(StrEnum):
    """Why a chat is paused."""

    OPERATOR = "operator"
    BUDGET = "budget"


class MessageStatus(StrEnum):
    """Whether a turn has taken a message as input."""

    QUEUED = "queued"
    CONSUMED = "consumed"
    SUPERSEDED = "superseded"
    """Replaced by a revision, or part of history that a revision rewound."""


class MessageKind(StrEnum):
    """Who wrote a message."""

    USER = "user"
    ANSWER = "answer"
    """The operator's answer to a question that is no longer waited for in its tool call."""


class TurnStatus(StrEnum):
    """Turn outcome; ``running`` while in progress."""

    RUNNING = "running"
    COMPLETED = "completed"
    SUSPENDED = "suspended"
    INTERRUPTED = "interrupted"
    """New operator input ended the turn at a safe point; the next turn continues from it."""
    STOPPED = "stopped"
    LIMITED = "limited"
    FAILED = "failed"


class TurnTrigger(StrEnum):
    """What started a turn."""

    INPUT = "input"
    """Operator messages, decisions or answers."""
    WAKEUP = "wakeup"
    """A wakeup the agent scheduled in Proactive mode."""
    EVENT = "event"
    """A job or Observe alert reported to a chat in Proactive mode."""


class TitleSource(StrEnum):
    """Who set the chat title."""

    FALLBACK = "fallback"
    AUTO = "auto"
    USER = "user"
    """Set by the operator; automatic titles no longer change it."""


class QuestionStatus(StrEnum):
    """Lifecycle of an agent question."""

    PENDING = "pending"
    ANSWERED = "answered"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class CredentialStatus(StrEnum):
    """Whether a credential may be used."""

    ACTIVE = "active"
    REVOKED = "revoked"


class SecretType(StrEnum):
    """What a credential's secret is and how it is presented."""

    TOKEN = "token"  # noqa: S105 - the type's name
    """A value sent in a request header after a prefix."""
    LOGIN = "login"
    """A username and a password typed into login forms of the Computer's browser."""


class CredentialGrant(StrEnum):
    """A mechanism a credential is granted to."""

    AGENT = "agent"
    """Tools of the agent: `web_fetch` headers and browser logins, only on the credential hosts."""
    WATCH = "watch"
    """Observe polls, only on the credential hosts."""
    ENV = "env"
    """An environment variable of the chat's `code_run` executions."""


class WakeupStatus(StrEnum):
    """Lifecycle of a scheduled wakeup."""

    PENDING = "pending"
    FIRED = "fired"
    CANCELLED = "cancelled"


class ActionStatus(StrEnum):
    """Lifecycle of one tool call."""

    DENIED = "denied"
    AWAITING_APPROVAL = "awaiting_approval"
    SUSPENDED = "suspended"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNCERTAIN = "uncertain"


class Decision(StrEnum):
    """Operator decision on an action."""

    APPROVE = "approve"
    REJECT = "reject"
    CANCEL = "cancel"


class ExecutionKind(StrEnum):
    """What runs a job."""

    CODE = "code"
    """A `code_run` container or an API run."""
    TERMINAL = "terminal"
    """A `terminal` command in the Computer."""


DECISION_STATUS = {
    Decision.APPROVE: ActionStatus.APPROVED,
    Decision.REJECT: ActionStatus.REJECTED,
    Decision.CANCEL: ActionStatus.CANCELLED,
}
PENDING_ACTION_STATUSES = frozenset({ActionStatus.AWAITING_APPROVAL, ActionStatus.SUSPENDED})
FINAL_ACTION_STATUSES = frozenset(
    {
        ActionStatus.DENIED,
        ActionStatus.REJECTED,
        ActionStatus.EXPIRED,
        ActionStatus.SUCCEEDED,
        ActionStatus.FAILED,
        ActionStatus.CANCELLED,
        ActionStatus.UNCERTAIN,
    }
)


class WorkspaceStatus(StrEnum):
    """Where the chat's volumes are."""

    ACTIVE = "active"
    HIBERNATED = "hibernated"
    DELETED = "deleted"


class ExecutionStatus(StrEnum):
    """Lifecycle of one Execute run."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"
    UNCERTAIN = "uncertain"


FINAL_EXECUTION_STATUSES = frozenset(
    {
        ExecutionStatus.SUCCEEDED,
        ExecutionStatus.FAILED,
        ExecutionStatus.CANCELLED,
        ExecutionStatus.TIMEOUT,
        ExecutionStatus.UNCERTAIN,
    }
)


class ToolGroup(StrEnum):
    """Tool groups a chat enables."""

    OBSERVE = "observe"
    EXECUTE = "execute"
    COMPUTE = "compute"
    FILES = "files"
    PROACTIVE = "proactive"
    """Wakeups the agent schedules; enabled by the chat's ``proactive`` setting."""
    ALWAYS = "always"
    """Tools every chat has: capability and credential requests."""


SELECTABLE_GROUPS = frozenset({ToolGroup.OBSERVE, ToolGroup.EXECUTE, ToolGroup.COMPUTE})
"""Groups a chat stores in ``tool_groups``; the others follow from other settings."""


class SourceStatus(StrEnum):
    """Whether an Observe source is polled."""

    DRAFT = "draft"
    PUBLISHED = "published"
    PAUSED = "paused"


class SourcePauseReason(StrEnum):
    """Why a source is paused."""

    OPERATOR = "operator"
    AUTH_LOST = "auth_lost"


class AlertKind(StrEnum):
    """What an Observe alert reports."""

    CHANGED = "changed"
    GONE = "gone"
    UNAVAILABLE = "unavailable"
    AUTH_LOST = "auth_lost"
    LIMIT = "limit"
    STALE = "stale"
    SCHEMA_CHANGED = "schema_changed"


class AlertStatus(StrEnum):
    """Alert lifecycle: ``open → acknowledged → resolved``."""

    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"


class SubscriptionChannel(StrEnum):
    """Where alerts are delivered."""

    UI = "ui"
    WEBHOOK = "webhook"


class Locale(StrEnum):
    """Interface language saved on the user."""

    EN = "en"
    RU = "ru"


class Base(DeclarativeBase):
    """Base class of all tables, with deterministic constraint names for migrations."""

    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(column_0_label)s",
            "uq": "uq_%(table_name)s_%(column_0_N_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )
    type_annotation_map = {  # noqa: RUF012
        enum.Enum: Enum(
            enum.Enum,
            native_enum=False,
            length=32,
            values_callable=lambda members: [member.value for member in members],
        ),
        uuid.UUID: UUID(as_uuid=True),
        datetime: DateTime(timezone=True),
        # JsonValue includes None, so required JSONB columns set nullable=False explicitly.
        JsonValue: JSONB,
    }


def _now() -> Mapped[datetime]:
    return mapped_column(server_default=func.now())


def _updated() -> Mapped[datetime]:
    return mapped_column(server_default=func.now(), onupdate=func.now())


def _owner() -> Mapped[uuid.UUID]:
    return mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)


def _actor() -> Mapped[uuid.UUID | None]:
    return mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class User(Base):
    """A person who signs in to Xavier; everything they create is visible only to them."""

    __tablename__ = "users"
    __table_args__ = (
        Index(
            "uq_users_username",
            "username",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)

    username: Mapped[str] = mapped_column(Text)
    """Lowercase sign-in name, unique among users that are not deleted."""
    name: Mapped[str] = mapped_column(Text)
    locale: Mapped[Locale | None]
    """Interface language. Empty until the user signs in from a browser."""
    password_hash: Mapped[str] = mapped_column(Text)
    """Argon2id hash of the password."""

    created_at: Mapped[datetime] = _now()

    updated_at: Mapped[datetime] = _updated()
    updated_by: Mapped[uuid.UUID | None] = _actor()

    deleted_at: Mapped[datetime | None]
    deleted_by: Mapped[uuid.UUID | None] = _actor()
    """``NULL`` when deleted from the command line."""


class AuthSession(Base):
    """A signed-in browser: its cookie value is stored only as a SHA-256 hash."""

    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)

    user_id: Mapped[uuid.UUID] = _owner()

    token_hash: Mapped[str] = mapped_column(Text, unique=True)
    user_agent: Mapped[str] = mapped_column(Text, default="")
    ip: Mapped[str | None] = mapped_column(Text)

    last_seen_at: Mapped[datetime] = _now()
    expires_at: Mapped[datetime]
    revoked_at: Mapped[datetime | None]

    created_at: Mapped[datetime] = _now()


class ApiKey(Base):
    """A key for programmatic access: stored as its prefix and the SHA-256 hash of the key."""

    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)

    prefix: Mapped[str] = mapped_column(Text, unique=True)
    """Public start of the key that identifies it, such as ``xv_3fA9c2Qk``."""

    user_id: Mapped[uuid.UUID] = _owner()

    name: Mapped[str] = mapped_column(Text)
    key_hash: Mapped[str] = mapped_column(Text)

    last_used_at: Mapped[datetime | None]
    revoked_at: Mapped[datetime | None]

    created_at: Mapped[datetime] = _now()


class Chat(Base):
    """A conversation with the agent."""

    __tablename__ = "chats"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)

    title: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(Text)
    reasoning: Mapped[str] = mapped_column(Text)
    mode: Mapped[Mode]
    tool_groups: Mapped[list[str]] = mapped_column(ARRAY(Text))
    execute_network: Mapped[bool]
    proactive: Mapped[bool] = mapped_column(default=False)
    """The agent may schedule wakeups and react to events without new input."""
    declined_capabilities: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    """Capabilities the operator declined to enable; the agent may not request them again
    until the operator changes that setting."""
    budget_usd: Mapped[float]
    max_steps: Mapped[int | None]
    """Step limit of one turn. ``None`` sets no step limit."""

    status: Mapped[ChatStatus]
    status_version: Mapped[int] = mapped_column(BigInteger, default=0)
    pause_reason: Mapped[PauseReason | None]
    title_source: Mapped[TitleSource] = mapped_column(default=TitleSource.FALLBACK)

    spent_usd: Mapped[float] = mapped_column(default=0.0)

    snapshot_manifest: Mapped[str | None] = mapped_column(Text)
    """S3 key of the session snapshot the next turn resumes."""

    created_at: Mapped[datetime] = _now()
    created_by: Mapped[uuid.UUID] = _owner()
    """The user who owns the chat."""

    updated_at: Mapped[datetime] = _updated()
    updated_by: Mapped[uuid.UUID | None] = _actor()

    deleted_at: Mapped[datetime | None]
    """Hidden from every list and lookup; purged after ``retention.deleted``."""
    deleted_by: Mapped[uuid.UUID | None] = _actor()


class Message(Base):
    """Operator input to a chat."""

    __tablename__ = "messages"
    __table_args__ = (
        Index("ix_messages_chat_id_id", "chat_id", "id"),
        Index("ix_messages_queued", "chat_id", postgresql_where=text("status = 'queued'")),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)

    chat_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"))

    turn_id: Mapped[uuid.UUID | None]
    """Turn that consumed the message. Not a foreign key: a rewind keeps the message, and
    deleting the chat removes the message and the turn together."""
    question_id: Mapped[uuid.UUID | None]
    """Question an ``answer`` message answers. Not a foreign key: the answer outlives the
    question when that question's turn is removed."""
    revision_of: Mapped[uuid.UUID | None]
    """Message this one revises; revisions of a revision point to the original. Not a foreign
    key, so deleting the chat can remove a revision and its original together."""

    text: Mapped[str] = mapped_column(Text)
    image_artifact_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(UUID(as_uuid=True)))
    file_artifact_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(UUID(as_uuid=True)))

    status: Mapped[MessageStatus]
    kind: Mapped[MessageKind] = mapped_column(default=MessageKind.USER)

    superseded_at: Mapped[datetime | None]

    created_at: Mapped[datetime] = _now()


class Turn(Base):
    """One agent pass over the accumulated input."""

    __tablename__ = "turns"
    __table_args__ = (Index("ix_turns_chat_id_id", "chat_id", "id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)

    worker_id: Mapped[str | None] = mapped_column(Text)

    chat_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"))

    final_response: Mapped[str | None] = mapped_column(Text)
    trigger: Mapped[TurnTrigger] = mapped_column(default=TurnTrigger.INPUT)

    status: Mapped[TurnStatus]
    reason: Mapped[str | None] = mapped_column(Text)
    attempt: Mapped[int] = mapped_column(default=0)
    """Activity attempt that owns the turn; writes of older attempts are rejected."""

    steps: Mapped[int] = mapped_column(default=0)
    input_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    output_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    cost_usd: Mapped[float] = mapped_column(default=0.0)
    started_at: Mapped[datetime] = _now()
    finished_at: Mapped[datetime | None]
    superseded_at: Mapped[datetime | None]
    """A revision of earlier input rewound the history before this turn."""

    checkpoint_manifest: Mapped[str | None] = mapped_column(Text)
    """Session snapshot at the end of the turn; a revision rewinds to it."""


class Action(Base):
    """One tool call with its moderation and outcome."""

    __tablename__ = "actions"
    __table_args__ = (
        Index("ix_actions_chat_id_id", "chat_id", "id"),
        Index("ix_actions_turn_id", "turn_id"),
        Index(
            "ix_actions_pending",
            "id",
            postgresql_where=text("status IN ('awaiting_approval', 'suspended')"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)

    chat_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"))
    turn_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("turns.id", ondelete="CASCADE"))

    rule_id: Mapped[uuid.UUID | None]
    """Rule that matched. Kept after that rule is deleted."""

    tool: Mapped[str] = mapped_column(Text)
    arguments: Mapped[JsonValue] = mapped_column(nullable=False)
    """Normalized arguments with secrets redacted."""
    pending_arguments: Mapped[JsonValue | None]
    """Exact arguments of an action waiting for a decision; cleared when it finishes."""
    args_hash: Mapped[str] = mapped_column(Text)
    resources: Mapped[JsonValue] = mapped_column(nullable=False)

    risk: Mapped[Risk]
    effect: Mapped[Effect]
    status: Mapped[ActionStatus]
    decision: Mapped[Decision | None]
    attempt: Mapped[int]
    error_code: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)

    decided_at: Mapped[datetime | None]
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]

    result: Mapped[JsonValue | None]

    created_at: Mapped[datetime] = _now()


class Event(Base):
    """Chat feed entry. Ids are assigned by PostgreSQL in commit order per chat."""

    __tablename__ = "events"
    __table_args__ = (Index("ix_events_chat_id_id", "chat_id", "id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=func.uuidv7())

    chat_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"))

    turn_id: Mapped[uuid.UUID | None]
    """Turn that wrote the event. Not a foreign key: the feed stays after the turn is
    superseded, and deleting the chat removes both."""

    kind: Mapped[str] = mapped_column(Text)
    data: Mapped[JsonValue] = mapped_column(nullable=False)

    created_at: Mapped[datetime] = _now()


class PolicyRule(Base):
    """A policy rule; ``chat_id`` ``NULL`` is global."""

    __tablename__ = "policy_rules"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)

    chat_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"))

    tool_pattern: Mapped[str] = mapped_column(Text)
    resource_pattern: Mapped[str | None] = mapped_column(Text)
    priority: Mapped[int] = mapped_column(default=0)
    description: Mapped[str] = mapped_column(Text, default="")

    effect: Mapped[Effect]

    created_at: Mapped[datetime] = _now()
    created_by: Mapped[uuid.UUID] = _owner()
    """The user whose chats the rule applies to."""

    updated_at: Mapped[datetime] = _updated()
    updated_by: Mapped[uuid.UUID | None] = _actor()


class Artifact(Base):
    """Metadata of a file stored in S3 under ``artifacts/<id>``."""

    __tablename__ = "artifacts"
    __table_args__ = (Index("ix_artifacts_chat_id_id", "chat_id", "id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)

    chat_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"))

    name: Mapped[str] = mapped_column(Text)
    content_type: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text)
    """``upload``, ``export``, ``output``, ``screenshot`` or ``result``."""

    size_bytes: Mapped[int] = mapped_column(BigInteger)

    created_at: Mapped[datetime] = _now()
    created_by: Mapped[uuid.UUID] = _owner()
    """The owner: the chat's owner, or the user who uploaded or ran it outside chats."""


class Worker(Base):
    """A live worker process and its internal server address."""

    __tablename__ = "workers"

    id: Mapped[str] = mapped_column(Text, primary_key=True)

    internal_url: Mapped[str] = mapped_column(Text)

    heartbeat_at: Mapped[datetime]


class Lease(Base):
    """An exclusive lease with a fencing token that grows with every acquisition."""

    __tablename__ = "leases"

    key: Mapped[str] = mapped_column(Text, primary_key=True)

    holder: Mapped[str | None] = mapped_column(Text)
    token: Mapped[int] = mapped_column(BigInteger, default=0)

    expires_at: Mapped[datetime | None]


class Workspace(Base):
    """The chat's ``workspace`` and ``home`` volumes: active on one host or a checkpoint in S3."""

    __tablename__ = "workspaces"

    chat_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE"), primary_key=True
    )

    host: Mapped[str | None] = mapped_column(Text)
    """Worker id while ``active``."""
    workspace_volume: Mapped[str | None] = mapped_column(Text)
    home_volume: Mapped[str | None] = mapped_column(Text)
    holder_id: Mapped[str | None] = mapped_column(Text)
    computer_id: Mapped[str | None] = mapped_column(Text)

    status: Mapped[WorkspaceStatus]
    epoch: Mapped[int] = mapped_column(BigInteger, default=0)
    """Grows with every activation; fences writes of a previous activation."""

    checkpoint_at: Mapped[datetime | None]
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    touched_at: Mapped[datetime] = _now()
    """Last tool call that used the workspace."""

    checkpoint: Mapped[str | None] = mapped_column(Text)
    """S3 prefix of the last checkpoint."""

    created_at: Mapped[datetime] = _now()


class Execution(Base):
    """One Execute run from a chat or from the API."""

    __tablename__ = "executions"
    __table_args__ = (
        Index("ix_executions_chat_id_id", "chat_id", "id"),
        Index(
            "ix_executions_unfinished",
            "id",
            postgresql_where=text("status IN ('pending', 'running')"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)

    chat_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"))
    """``NULL`` for API runs."""

    action_id: Mapped[uuid.UUID | None]
    """Action that started a chat run. Not a foreign key: deleting the chat removes the run
    and the action together, and an API run has none."""

    language: Mapped[str] = mapped_column(Text)
    code: Mapped[str | None] = mapped_column(Text)
    command: Mapped[str | None] = mapped_column(Text)
    network: Mapped[bool]
    export: Mapped[list[str]] = mapped_column(ARRAY(Text))
    input_artifact_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(UUID(as_uuid=True)))
    host: Mapped[str | None] = mapped_column(Text)
    container_id: Mapped[str | None] = mapped_column(Text)
    stdout: Mapped[str] = mapped_column(Text, default="")
    stderr: Mapped[str] = mapped_column(Text, default="")
    truncated: Mapped[bool] = mapped_column(default=False)
    """Output reached ``execute.output_limit``."""
    output_artifact_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), default=list
    )

    kind: Mapped[ExecutionKind] = mapped_column(default=ExecutionKind.CODE)
    status: Mapped[ExecutionStatus]

    exit_code: Mapped[int | None]
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]
    collected_at: Mapped[datetime | None]
    """Time the agent received the final result; an uncollected job that finishes after its
    turn is reported to the chat."""

    metrics: Mapped[JsonValue] = mapped_column(default=dict, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = _now()
    created_by: Mapped[uuid.UUID] = _owner()
    """The chat's owner, or the user who started an API run."""


class Credential(Base):
    """A secret sent to services on the agent's behalf, encrypted with ``credentials_key``.

    The agent refers to it by id; the value never reaches the model.
    """

    __tablename__ = "credentials"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)

    chat_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"))
    """Only this chat may use it; ``NULL`` lets every chat of the owner use it."""

    name: Mapped[str] = mapped_column(Text)
    secret_type: Mapped[SecretType]
    header: Mapped[str | None] = mapped_column(Text)
    """Request header of a ``token``."""
    prefix: Mapped[str] = mapped_column(Text, default="")
    """Text before the value of a ``token``, such as ``Bearer ``."""
    refresh_url: Mapped[str | None] = mapped_column(Text)
    """HTTPS token endpoint on one of ``hosts`` that renews a ``token`` with its refresh token."""
    username: Mapped[str | None] = mapped_column(Text)
    """User name of a ``login``; not secret."""
    secret: Mapped[str] = mapped_column(Text)
    """Fernet token of the JSON ``{"value": ..., "refresh_token": ...}``; a ``login`` keeps its
    password in ``value``."""
    hosts: Mapped[list[str]] = mapped_column(ARRAY(Text))
    """Host patterns (``example.com``, ``*.example.com``) the agent and Watch may send it to."""
    grant_agent: Mapped[bool] = mapped_column(default=False)
    grant_watch: Mapped[bool] = mapped_column(default=False)
    grant_env: Mapped[str | None] = mapped_column(Text)
    """Environment variable of the chat's ``code_run`` executions that holds the value."""

    status: Mapped[CredentialStatus] = mapped_column(default=CredentialStatus.ACTIVE)
    version: Mapped[int] = mapped_column(default=1)
    """Grows with every rotation of the secret."""

    expires_at: Mapped[datetime | None]
    last_used_at: Mapped[datetime | None]
    failed_at: Mapped[datetime | None]
    """Time the credential was rejected; cleared when its secret is replaced."""

    created_at: Mapped[datetime] = _now()
    created_by: Mapped[uuid.UUID] = _owner()
    """The owner; only their chats and sources may use the credential."""

    updated_at: Mapped[datetime] = _updated()
    updated_by: Mapped[uuid.UUID | None] = _actor()


class CredentialEvent(Base):
    """Audit record of a credential: created, rotated, revoked, used, rejected. Never a value."""

    __tablename__ = "credential_events"
    __table_args__ = (Index("ix_credential_events_credential_id_id", "credential_id", "id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=func.uuidv7())

    credential_id: Mapped[uuid.UUID]
    """Kept after the credential is deleted."""
    chat_id: Mapped[uuid.UUID | None]
    action_id: Mapped[uuid.UUID | None]

    kind: Mapped[str] = mapped_column(Text)
    detail: Mapped[str] = mapped_column(Text, default="")

    created_at: Mapped[datetime] = _now()


class Question(Base):
    """An ``ask_user_question`` call of the agent and its answer."""

    __tablename__ = "questions"
    __table_args__ = (
        Index("ix_questions_chat_id_id", "chat_id", "id"),
        Index("uq_questions_chat_id_call_id", "chat_id", "call_id", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)

    call_id: Mapped[str] = mapped_column(Text)
    """Harness tool call id; a late answer names it."""

    chat_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"))
    turn_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("turns.id", ondelete="CASCADE"))

    questions: Mapped[JsonValue] = mapped_column(nullable=False)
    """The Harness question items: ``id``, ``question``, ``options``, ``multiSelect``, ..."""
    answers: Mapped[JsonValue | None]
    timeout_s: Mapped[int]
    """Foreground wait of the call; ``-1`` waits until the operator answers."""

    status: Mapped[QuestionStatus]
    attempt: Mapped[int]
    live: Mapped[bool] = mapped_column(default=False)
    """The tool call still waits in a running turn; it receives the answer directly."""
    waiting: Mapped[bool] = mapped_column(default=False)
    """The turn suspended until the answer: the chat needs input."""

    answered_at: Mapped[datetime | None]

    created_at: Mapped[datetime] = _now()


class Wakeup(Base):
    """A future turn the agent scheduled in Proactive mode."""

    __tablename__ = "wakeups"
    __table_args__ = (
        Index(
            "ix_wakeups_pending", "chat_id", "due_at", postgresql_where=text("status = 'pending'")
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)

    chat_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"))

    action_id: Mapped[uuid.UUID | None]
    """Action that scheduled the wakeup. Not a foreign key: cancelling the action leaves the
    wakeup, and deleting the chat removes both."""

    reason: Mapped[str] = mapped_column(Text)

    status: Mapped[WakeupStatus]

    due_at: Mapped[datetime]
    fired_at: Mapped[datetime | None]

    created_at: Mapped[datetime] = _now()


class Source(Base):
    """An Observe source; ``spec`` is polled and ``draft`` is a revision awaiting publication."""

    __tablename__ = "sources"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)

    credential_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("credentials.id", ondelete="SET NULL")
    )
    """Credential of ``spec``."""
    chat_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("chats.id", ondelete="SET NULL"))
    """Chat whose agent drafted the source."""

    name: Mapped[str] = mapped_column(Text)
    spec: Mapped[JsonValue] = mapped_column(nullable=False)
    draft: Mapped[JsonValue | None]

    status: Mapped[SourceStatus]
    pause_reason: Mapped[SourcePauseReason | None]

    last_polled_at: Mapped[datetime | None]
    last_success_at: Mapped[datetime | None]

    last_error: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = _now()
    created_by: Mapped[uuid.UUID] = _owner()
    """The owner; a source drafted by an agent belongs to the chat's owner."""

    updated_at: Mapped[datetime] = _updated()
    updated_by: Mapped[uuid.UUID | None] = _actor()


class Version(Base):
    """A distinct observed value of one resource of a source, stored in S3 by its fingerprint."""

    __tablename__ = "versions"
    __table_args__ = (
        Index("ix_versions_source_id_resource_key_id", "source_id", "resource_key", "id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)

    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"))

    credential_id: Mapped[uuid.UUID | None]
    """Credential the value was fetched with. Kept after that credential is deleted, so each
    credential keeps its own history."""

    resource_key: Mapped[str] = mapped_column(Text)
    """``""`` for the source URL, ``<rule>:<value>`` for a child resource."""
    url: Mapped[str] = mapped_column(Text)
    fingerprint: Mapped[str | None] = mapped_column(Text)
    """SHA-256 of the canonical value and its S3 key ``observe/blobs/<sha256>``; ``NULL`` when
    the resource is gone."""
    content_type: Mapped[str] = mapped_column(Text)

    size_bytes: Mapped[int] = mapped_column(BigInteger)
    change_count: Mapped[int]

    structure: Mapped[JsonValue | None]
    schema_changes: Mapped[JsonValue] = mapped_column(nullable=False)
    changes: Mapped[JsonValue] = mapped_column(nullable=False)
    """Up to 50 value changes from the previous version."""

    created_at: Mapped[datetime] = _now()

    @property
    def gone(self) -> bool:
        """Whether the resource was gone."""
        return self.fingerprint is None


class Alert(Base):
    """An Observe alert; repeats of an unresolved one with the same key count as occurrences."""

    __tablename__ = "alerts"
    __table_args__ = (
        Index("ix_alerts_source_id_id", "source_id", "id"),
        Index(
            "uq_alerts_dedup_key_unresolved",
            "dedup_key",
            unique=True,
            postgresql_where=text("status <> 'resolved'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)

    dedup_key: Mapped[str] = mapped_column(Text)

    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"))

    version_id: Mapped[uuid.UUID | None]
    """Version that raised the alert. Not a foreign key: deleting the source removes the alert
    and the version together."""

    resource_key: Mapped[str] = mapped_column(Text)
    message: Mapped[str] = mapped_column(Text)
    data: Mapped[JsonValue] = mapped_column(nullable=False)

    kind: Mapped[AlertKind]
    status: Mapped[AlertStatus]
    occurrences: Mapped[int] = mapped_column(default=1)

    acknowledged_at: Mapped[datetime | None]
    resolved_at: Mapped[datetime | None]

    created_at: Mapped[datetime] = _now()

    updated_at: Mapped[datetime] = _updated()
    updated_by: Mapped[uuid.UUID | None] = _actor()


class Subscription(Base):
    """Delivery of alerts of one source, or of all sources, to the UI or a webhook."""

    __tablename__ = "subscriptions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)

    source_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE")
    )

    kinds: Mapped[list[str]] = mapped_column(ARRAY(Text))
    """Alert kinds delivered; empty delivers every kind."""
    url: Mapped[str | None] = mapped_column(Text)
    secret: Mapped[str | None] = mapped_column(Text)
    """Fernet token of the webhook signing secret."""

    channel: Mapped[SubscriptionChannel]

    created_at: Mapped[datetime] = _now()
    created_by: Mapped[uuid.UUID] = _owner()
    """The owner; a subscription without a source covers every source of the owner."""

    updated_at: Mapped[datetime] = _updated()
    updated_by: Mapped[uuid.UUID | None] = _actor()
