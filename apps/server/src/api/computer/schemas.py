"""The chat's Computer: status and control by the user."""

from datetime import datetime
from enum import StrEnum

from pydantic import Field

from src.api.common import Input, Result, Schema

# --- DATA MODEL --- #


class LifecycleStatus(StrEnum):
    """Where the workspace is."""

    ACTIVE = "active"
    HIBERNATED = "hibernated"
    DELETED = "deleted"
    NONE = "none"


class WorkspaceLifecycle(Schema):
    """This object represents where the workspace is and how much of its disk it uses."""

    status: LifecycleStatus = Field(description="*none* when the chat never used a workspace")
    host: str | None = Field(
        default=None, description="_Optional_. Worker hosting an active workspace"
    )
    checkpoint_at: datetime | None = Field(
        default=None, description="_Optional_. Date and time of the last checkpoint"
    )
    touched_at: datetime | None = Field(
        default=None, description="_Optional_. Date and time of the last use"
    )
    size_bytes: int = Field(
        default=0,
        description="Disk use, measured now when active, else as of the last checkpoint",
    )
    size_limit_bytes: int | None = Field(default=None, description="_Optional_. Disk limit")


class ComputerHealth(Schema):
    """This object represents the state and usage of the Computer container."""

    status: str = Field(description="Docker state: *running*, *exited*, *dead*, … or *gone*")
    oom_killed: bool = Field(default=False, description="_True_, if it ran out of memory")
    restarts: int = Field(default=0, description="Restarts since creation")
    started_at: str | None = Field(
        default=None, description="_Optional_. Date and time of the last start"
    )
    exit_code: int | None = Field(
        default=None, description="_Optional_. Exit code of a stopped container"
    )
    error: str | None = Field(default=None, description="_Optional_. Docker error")
    cpus: float | None = Field(default=None, description="_Optional_. CPU limit")
    cpu_ns: int | None = Field(default=None, description="_Optional_. CPU time used since start")
    memory_bytes: int | None = Field(default=None, description="_Optional_. Memory in use")
    memory_limit_bytes: int | None = Field(default=None, description="_Optional_. Memory limit")
    pids: int | None = Field(default=None, description="_Optional_. Processes")
    browser: bool | None = Field(default=None, description="_Optional_. _True_, if Chromium runs")
    jobs: int | None = Field(default=None, description="_Optional_. Running terminal jobs")


class FailureKind(StrEnum):
    """Kind of a workspace failure."""

    LOST = "lost"
    FULL = "full"
    COMPUTER_FAILED = "computer_failed"


class WorkspaceFailure(Schema):
    """This object represents a failure of the workspace or its Computer."""

    status: FailureKind = Field(description="Kind of failure")
    at: datetime = Field(description="Date and time of the failure")
    reason: str | None = Field(default=None, description="_Optional_. What went wrong")
    host: str | None = Field(default=None, description="_Optional_. Worker that reported it")


class ComputerStatus(Schema):
    """This object represents the lifecycle, usage and health of the workspace and Computer."""

    workspace: WorkspaceLifecycle = Field(description="Where the workspace is")
    computer: ComputerHealth | None = Field(
        description="_Optional_. *null* unless the workspace is active with a Computer"
    )
    failures: list[WorkspaceFailure] = Field(description="Recent failures, latest first")


class Control(Schema):
    """This object represents whether the user controls the Computer."""

    held: bool = Field(description="_True_, if the user has control")
    token: int | None = Field(
        default=None,
        description="_Optional_. Fencing token of the control; pass it to the control screen "
        "stream",
    )
    expires_at: datetime | None = Field(
        default=None,
        description="_Optional_. End of control unless a control screen stream keeps it",
    )


# --- INPUTS --- #


class ControlAction(StrEnum):
    """Change of the control of the Computer."""

    TAKE = "take"
    RELEASE = "release"


class UpdateControlInput(Input):
    """This object represents the input used to take or return control of the Computer."""

    action: ControlAction = Field(description="Take or return control")


# --- ARGS --- #


class UpdateControlArgs(Input):
    """This object represents the arguments used to change the control of the Computer."""

    data: UpdateControlInput


# --- RESULTS --- #


class ReadComputerStatusResult(Result[ComputerStatus]):
    """This object represents the result when the Computer status is read."""


class UpdateControlResult(Result[Control]):
    """This object represents the result when the control of the Computer is changed."""
