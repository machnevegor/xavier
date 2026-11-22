"""The chat workspace and its files."""

from datetime import datetime
from enum import StrEnum

from pydantic import Field

from src.api.common import Input, Result, Schema
from src.db import models as db

# --- DATA MODEL --- #


class Workspace(Schema):
    """This object represents the chat workspace."""

    status: db.WorkspaceStatus | None = Field(
        description="*active* with running volumes, *hibernated* as a checkpoint; *null* when "
        "the chat never used a workspace"
    )
    size_bytes: int = Field(default=0, description="Disk use as of the last measurement")
    checkpoint_at: datetime | None = Field(
        default=None, description="_Optional_. Date and time of the last checkpoint"
    )
    touched_at: datetime | None = Field(
        default=None, description="_Optional_. Date and time of the last use"
    )


class FileKind(StrEnum):
    """Kind of a directory entry."""

    FILE = "file"
    DIR = "dir"
    LINK = "link"
    OTHER = "other"


class WorkspaceFile(Schema):
    """This object represents an entry of a workspace directory."""

    name: str = Field(description="Name of the entry")
    kind: FileKind = Field(description="Kind of the entry")
    size: int = Field(description="Size in bytes")
    modified: float = Field(description="Time of the last change, Unix time")


class WorkspaceDirectory(Schema):
    """This object represents the entries of a workspace directory."""

    path: str = Field(description="Absolute path of the directory")
    entries: list[WorkspaceFile] = Field(description="Entries of the directory")


# --- QUERY PARAMS --- #


class ListWorkspaceFilesInput(Input):
    """This object represents the input used to list a workspace directory."""

    path: str = Field(
        default="/workspace", description="_Optional_. Absolute path of the directory"
    )


class ReadWorkspaceFileInput(Input):
    """This object represents the input used to download a workspace file."""

    path: str = Field(description="Absolute path of the file")


# --- RESULTS --- #


class ReadWorkspaceResult(Result[Workspace]):
    """This object represents the result when the workspace is read."""


class WakeWorkspaceResult(Result[Workspace]):
    """This object represents the result when waking the workspace is requested."""


class HibernateWorkspaceResult(Result[Workspace]):
    """This object represents the result when hibernating the workspace is requested."""


class DeleteWorkspaceResult(Result[Workspace]):
    """This object represents the result when deleting the workspace is requested."""


class ListWorkspaceFilesResult(Result[WorkspaceDirectory]):
    """This object represents the result when a workspace directory is listed."""
