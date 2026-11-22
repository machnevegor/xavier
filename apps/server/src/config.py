"""Application settings loaded from ``settings.yaml`` and ``XAVIER_*`` environment variables."""

import socket
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Literal, Self

from cryptography.fernet import Fernet
from pydantic import BaseModel, ByteSize, ConfigDict, Field, SecretStr, model_validator
from pydantic_settings import (
    BaseSettings,
    EnvSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)
from sqlalchemy import URL

from src.policy.model import Mode


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_attribute_docstrings=True)


class ApiSettings(_Section):
    """HTTP server of the ``api`` process."""

    host: str = "127.0.0.1"
    """Listen address."""
    port: int = Field(default=8000, ge=1, le=65535)
    """Listen port."""
    forwarded_allow_ips: list[str] = []
    """Reverse proxies trusted to set ``X-Forwarded-*`` headers. Empty disables the headers."""


class AuthSettings(_Section):
    """Sign-in sessions of users; manage users with ``python -m src users``."""

    session_ttl: timedelta = timedelta(days=30)
    """Time after sign-in when a session ends regardless of use."""
    session_idle_ttl: timedelta = timedelta(days=7)
    """Time without requests after which a session ends."""


class WorkerSettings(_Section):
    """The ``worker`` process: Temporal worker, MCP gateway and the internal server."""

    id: str = Field(default_factory=socket.gethostname)
    """Stable host identity; names the host task queue. Defaults to the hostname."""
    max_workspaces: int = Field(default=8, ge=1)
    """Active workspaces this host keeps at most."""
    internal_host: str = "127.0.0.1"
    """Listen address of the internal server for ``api``.

    A wildcard address (``0.0.0.0``, ``::``) is advertised to ``api`` as the worker id, which must
    then resolve to this host, as a compose service name does.
    """
    internal_port: int = Field(default=8081, ge=1, le=65535)
    """Listen port of the internal server."""
    internal_token: SecretStr
    """Token ``api`` presents to the internal server."""
    max_concurrent_activities: int = Field(default=32, ge=1)
    """Activities this worker runs at once on each task queue."""

    def internal_url(self) -> str:
        """Return the address ``api`` uses to reach the internal server."""
        host = self.id if self.internal_host in {"0.0.0.0", "::"} else self.internal_host  # noqa: S104
        return f"http://{host}:{self.internal_port}"


class ProviderSettings(_Section):
    """An LLM provider reached through the Harness ``llm-pi-ai`` plugin."""

    api: Literal["openai-completions"] = "openai-completions"
    """Wire protocol of the provider."""
    base_url: str
    api_key: SecretStr


class PricingSettings(_Section):
    """Model prices in USD per million tokens."""

    input: float = Field(ge=0)
    output: float = Field(ge=0)
    cache_read: float | None = Field(default=None, ge=0)
    """Price of cached input tokens. ``null`` charges them as input."""


class ModelSettings(_Section):
    """A model offered to chats."""

    id: str
    """Identifier chats refer to."""
    provider: str
    """Key in ``agent.providers``."""
    provider_model: str
    """Model name sent to the provider."""
    input: list[Literal["text", "image"]] = ["text"]
    """Accepted input kinds. Images reach only models with ``image``."""
    thinking_format: str | None = None
    """Reasoning wire format of the provider (``compat.thinkingFormat``)."""
    reasoning_levels: dict[str, str | None] = Field(default={"off": None}, min_length=1)
    """Reasoning levels a chat may choose, mapped to the provider value."""
    pricing_per_mtok: PricingSettings


class AgentSettings(_Section):
    """Agent runtime and models."""

    cwd: Path = Path("/var/lib/xavier/agent")
    """Empty working directory of the Harness runtime. Must be the same path on every worker:
    Harness stores sessions under a directory derived from it."""
    providers: dict[str, ProviderSettings]
    models: list[ModelSettings] = Field(min_length=1)
    default_model: str
    """Model of new chats; an ``id`` from ``models``."""

    @model_validator(mode="after")
    def _check_references(self) -> Self:
        ids = [model.id for model in self.models]
        if len(set(ids)) != len(ids):
            raise ValueError("agent.models ids must be unique")
        for model in self.models:
            if model.provider not in self.providers:
                raise ValueError(
                    f"model {model.id!r} refers to unknown provider {model.provider!r}"
                )
        if self.default_model not in ids:
            raise ValueError(f"agent.default_model {self.default_model!r} is not in agent.models")
        return self

    def model(self, model_id: str) -> ModelSettings:
        """Return the model ``model_id``.

        Raises
        ------
        KeyError
            If no model has this id.
        """
        for model in self.models:
            if model.id == model_id:
                return model
        raise KeyError(model_id)


class TurnSettings(_Section):
    """Limits of one agent turn."""

    timeout: timedelta = timedelta(minutes=30)
    max_steps: int = Field(default=50, ge=1)
    """Step limit stored on a new chat when the request omits it."""
    stop_grace: timedelta = timedelta(seconds=10)
    """Time running actions get to finish when a turn stops."""


class ApprovalSettings(_Section):
    """Operator decisions on actions."""

    hot_wait: timedelta = timedelta(seconds=120)
    """Time a tool call waits for a decision before the turn is suspended."""
    ttl: timedelta = timedelta(hours=24)
    """Time a suspended action waits for a decision before it expires."""


class ProactiveSettings(_Section):
    """Limits of turns the agent starts itself in chats with Proactive mode."""

    max_wakeups_per_day: int = Field(default=24, ge=1)
    """Turns of one chat started by wakeups or events in a sliding day."""
    min_interval: timedelta = timedelta(minutes=10)
    """Least time between the starts of two such turns."""
    budget_usd_per_wake: float = Field(default=0.5, ge=0)
    """What one such turn may spend at most, within the chat budget."""
    max_pending_wakeups: int = Field(default=10, ge=1)
    max_delay: timedelta = timedelta(days=30)
    """Farthest a wakeup may be scheduled."""


class ChatSettings(_Section):
    """Chat defaults and lifetime."""

    idle_ttl: timedelta = timedelta(days=7)
    """Idle time after which a ready chat's workflow completes; a new message restarts it."""
    default_mode: Mode = Mode.SUPERVISED
    default_budget_usd: float = Field(default=5.0, ge=0)


class WorkspaceSettings(_Section):
    """The chat's ``/workspace`` and ``home`` volumes."""

    idle_ttl: timedelta = timedelta(minutes=15)
    """Idle time after the last tool call that used the workspace before it hibernates."""
    size_limit: ByteSize = ByteSize(2 * 1024**3)
    """Limit of both volumes together."""


class ExecuteSettings(_Section):
    """Isolated code runs."""

    image: str = "xavier-sandbox:local"
    """Image of runs and of the workspace holder."""
    runtime: Literal["runc", "runsc"] = "runc"
    """OCI runtime; ``runsc`` when gVisor is installed."""
    max_duration: timedelta = timedelta(minutes=10)
    cpus: float = Field(default=1.0, gt=0)
    memory: ByteSize = ByteSize(1024**3)
    pids: int = Field(default=256, ge=16)
    tmp_size: ByteSize = ByteSize(256 * 1024**2)
    """Size of the ``/tmp`` tmpfs, and of ``/workspace`` for API runs."""
    output_limit: ByteSize = ByteSize(1024**2)
    """Kept bytes of stdout and of stderr each."""


class ComputerSettings(_Section):
    """The chat's Computer: desktop, Chromium and terminal in the workspace."""

    image: str = "xavier-desktop:local"
    pool_size: int = Field(default=1, ge=0)
    """Ready Computers with empty volumes this host keeps for new workspaces."""
    cpus: float = Field(default=2.0, gt=0)
    memory: ByteSize = ByteSize(2 * 1024**3)
    pids: int = Field(default=512, ge=64)
    control_ttl: timedelta = timedelta(minutes=10)
    """Lease of user control; renewed while the control screen is open."""


class ObserveSettings(_Section):
    """Polling of Observe sources; source limits may only lower these."""

    credentials_key: SecretStr
    """Fernet key encrypting credential secrets: 32 url-safe base64-encoded bytes."""
    max_response_bytes: ByteSize = ByteSize(5 * 1024**2)
    fetch_timeout: timedelta = timedelta(seconds=30)
    max_depth: int = Field(default=3, ge=0)
    """Levels of child resources below a source."""
    max_fanout: int = Field(default=100, ge=1)
    """Child resources one resource may enumerate."""

    @model_validator(mode="after")
    def _check_key(self) -> Self:
        try:
            Fernet(self.credentials_key.get_secret_value())
        except ValueError as error:
            raise ValueError(f"observe.credentials_key is not a Fernet key: {error}") from error
        return self


class RetentionSettings(_Section):
    """How long data outlives its use."""

    checkpoints: timedelta = timedelta(days=30)
    """Checkpoints of a hibernated workspace."""
    deleted: timedelta = timedelta(days=30)
    """A deleted chat with everything it owns, before it is purged."""
    executions: timedelta = timedelta(days=30)
    """API executions and their artifacts."""


class DatabaseSettings(_Section):
    """PostgreSQL connection of the application database."""

    host: str = "127.0.0.1"
    port: int = Field(default=5432, ge=1, le=65535)
    name: str = "xavier"
    user: str = "xavier"
    password: SecretStr

    def url(self) -> URL:
        """Return the SQLAlchemy URL for the asyncpg driver."""
        return URL.create(
            "postgresql+asyncpg",
            username=self.user,
            password=self.password.get_secret_value(),
            host=self.host,
            port=self.port,
            database=self.name,
        )

    def dsn(self) -> str:
        """Return the libpq URL for direct asyncpg connections such as ``LISTEN``."""
        return self.url().set(drivername="postgresql").render_as_string(hide_password=False)


class S3Settings(_Section):
    """S3-compatible object storage."""

    endpoint_url: str | None = None
    """Custom endpoint such as RustFS. ``null`` selects AWS S3."""
    region: str = "us-east-1"
    addressing_style: Literal["path", "virtual"] = "path"
    """Bucket addressing. Self-hosted endpoints usually require ``path``."""
    access_key_id: SecretStr
    secret_access_key: SecretStr
    artifacts_bucket: str = "xavier-artifacts"
    """Artifacts, screenshots, large results and agent session snapshots."""
    temporal_bucket: str = "xavier-temporal"
    """Large Temporal payloads offloaded by External Storage."""
    temporal_payload_ttl_days: int = Field(default=90, ge=1)
    """Lifecycle expiration of the Temporal bucket.

    Temporal never deletes offloaded payloads, so the bucket expires them. The value must exceed
    the longest workflow run plus the namespace retention period.
    """


class TemporalSettings(_Section):
    """Temporal Service connection."""

    address: str = "127.0.0.1:7233"
    namespace: str = "default"
    task_queue: str = "xavier"
    """Shared task queue for work that is not bound to a host."""
    payload_size_threshold: int = Field(default=256 * 1024, ge=0)
    """Payloads of at least this many bytes are offloaded to the Temporal bucket."""


class LoggingSettings(_Section):
    """Process logging."""

    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    format: Literal["json", "console"] = "json"
    """``json`` for production, ``console`` for development with clickable file references."""


class TelemetrySettings(_Section):
    """OpenTelemetry traces and metrics."""

    otlp_endpoint: str | None = None
    """Collector address. ``https://host`` is OTLP/HTTP (``/v1/traces`` and ``/v1/metrics``);
    ``host:4317`` is OTLP/gRPC. ``null`` exports nothing."""
    api_key: SecretStr | None = None
    """Ingestion key sent as the ``authorization`` header. ``null`` sends none."""
    service_name: str = "xavier"
    """Prefix of the service names; the process name follows, e.g. ``xavier-api``."""


class Settings(BaseSettings):
    """Complete Xavier configuration."""

    model_config = SettingsConfigDict(
        extra="forbid",
        frozen=True,
        use_attribute_docstrings=True,
        env_prefix="XAVIER_",
        env_nested_delimiter="__",
    )

    api: ApiSettings = ApiSettings()
    auth: AuthSettings = AuthSettings()
    worker: WorkerSettings
    database: DatabaseSettings
    s3: S3Settings
    temporal: TemporalSettings = TemporalSettings()
    logging: LoggingSettings = LoggingSettings()
    agent: AgentSettings
    turn: TurnSettings = TurnSettings()
    approval: ApprovalSettings = ApprovalSettings()
    chat: ChatSettings = ChatSettings()
    proactive: ProactiveSettings = ProactiveSettings()
    workspace: WorkspaceSettings = WorkspaceSettings()
    execute: ExecuteSettings = ExecuteSettings()
    computer: ComputerSettings = ComputerSettings()
    observe: ObserveSettings
    retention: RetentionSettings = RetentionSettings()
    telemetry: TelemetrySettings = TelemetrySettings()

    def secret_values(self) -> list[str]:
        """Return every configured secret value, for log redaction."""

        def walk(value: object) -> Iterator[str]:
            match value:
                case SecretStr():
                    yield value.get_secret_value()
                case BaseModel():
                    for name in type(value).model_fields:
                        yield from walk(getattr(value, name))
                case dict():
                    for item in value.values():
                        yield from walk(item)
                case list() | tuple():
                    for item in value:
                        yield from walk(item)
                case _:
                    pass

        return list(walk(self))


def load_settings(path: Path) -> Settings:
    """Load settings from ``path``; ``XAVIER_*`` environment variables take precedence.

    Raises
    ------
    FileNotFoundError
        If ``path`` does not exist.
    """
    if not path.is_file():
        raise FileNotFoundError(f"settings file not found: {path}")
    sources = (EnvSettingsSource(Settings), YamlConfigSettingsSource(Settings, yaml_file=path))
    return Settings(_build_sources=(sources, {}))
