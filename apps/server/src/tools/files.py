"""Files of the chat /workspace."""

import asyncio
from typing import Literal, Self
from uuid import UUID
from weakref import WeakValueDictionary

from pydantic import Field, JsonValue, model_validator

from src.db.models import ToolGroup
from src.errors import NotFoundError
from src.policy.model import Resource, ResourceKind, Risk
from src.policy.urls import WORKSPACE_ROOT, ResourceRejectedError, workspace_path
from src.tools.base import Tool, ToolArgs, ToolContext, ToolError, ToolResult, use_workspace
from src.workspace.service import Active, WorkspaceError

MAX_READ_CHARS = 100_000
MAX_WRITE_CHARS = 100_000
MAX_ENTRIES = 500
EDIT_MAX_BYTES = 1 << 20


class FilesArgs(ToolArgs):
    """Arguments of `files`."""

    op: Literal["list", "read", "export", "import", "write", "edit"] = Field(
        description=(
            "`list` a directory, `read` a text file, `write` or `edit` a text file, "
            "`export` a file as an artifact for the user, `import` an artifact into a file."
        )
    )
    path: str = Field(
        default=WORKSPACE_ROOT, description="Path under /workspace; relative paths start there."
    )
    artifact_id: UUID | None = Field(default=None, description="Artifact to `import`.")
    max_chars: int = Field(
        default=20_000, ge=1, le=MAX_READ_CHARS, description="Characters `read` returns."
    )
    text: str | None = Field(
        default=None,
        max_length=MAX_WRITE_CHARS,
        description="Full text `write` stores. At most 100000 characters.",
    )
    old: str | None = Field(
        default=None,
        max_length=MAX_WRITE_CHARS,
        description="Literal text `edit` replaces. It must occur once unless `replace_all`.",
    )
    new: str | None = Field(
        default=None,
        max_length=MAX_WRITE_CHARS,
        description="Replacement for `old`. An empty string deletes the match.",
    )
    replace_all: bool = Field(
        default=False, description="Replace every `old` match. Defaults to false."
    )

    @model_validator(mode="after")
    def _fields_match_the_operation(self) -> Self:
        if (self.op == "import") != (self.artifact_id is not None):
            raise ValueError("`artifact_id` is required for `import` and only for it")
        if (self.op == "write") != (self.text is not None):
            raise ValueError("`text` is required for `write` and only for it")
        if (self.op == "edit") != (self.old is not None):
            raise ValueError("`old` is required for `edit` and only for it")
        if (self.op == "edit") != (self.new is not None):
            raise ValueError("`new` is required for `edit` and only for it")
        if self.replace_all and self.op != "edit":
            raise ValueError("`replace_all` is only for `edit`")
        if self.op == "edit":
            if not self.old:
                raise ValueError("`old` must be non-empty")
            if self.old == self.new:
                raise ValueError("`old` and `new` must differ")
        if self.op in {"write", "edit"} and _file_path(self.path) == WORKSPACE_ROOT:
            raise ValueError("`write` and `edit` need a file path")
        return self


class Files(Tool[FilesArgs]):
    """List, read, write, edit, export and import files of the chat /workspace."""

    name = "files"
    groups = frozenset({ToolGroup.FILES})
    description = (
        "Work with files in the chat /workspace, which code runs and the Computer share and "
        "which persists across sessions. `list` shows a directory, `read` returns text, "
        "`write` creates or replaces a text file, `edit` replaces a literal fragment, "
        "`export` turns a file into an artifact the user can download (the way to deliver "
        "results), `import` writes an artifact, such as a user upload, into a file. "
        "Use `write` and `edit` to change files; do not write a program just to save one."
    )
    consequences = (
        "`write` and `edit` create or change a file; `export` publishes a file to the user; "
        "`import` creates or replaces a file."
    )
    risk = Risk.READ
    interrupt_safe = True
    args_type = FilesArgs

    def __init__(self) -> None:
        # Workspaces are bound to one host, so the parallel calls of a turn meet here.
        self._paths: WeakValueDictionary[tuple[UUID, str], asyncio.Lock] = WeakValueDictionary()

    async def classify(self, args: FilesArgs, ctx: ToolContext) -> Risk:
        """Rate listing and reading as ``read``, and every other operation as ``write``."""
        del ctx
        return Risk.READ if args.op in {"list", "read"} else Risk.WRITE

    def prepare(self, args: FilesArgs) -> FilesArgs:
        """Normalize the path."""
        return args.model_copy(update={"path": workspace_path(args.path)})

    def resources(self, args: FilesArgs) -> list[Resource]:
        """Return the path."""
        return [Resource(ResourceKind.PATH, args.path)]

    async def run(self, args: FilesArgs, ctx: ToolContext) -> ToolResult:
        """Perform the operation in the workspace holder; one at a time per path."""
        active, extra = await use_workspace(ctx)
        lock = self._paths.setdefault((ctx.chat_id, args.path), asyncio.Lock())
        async with lock:
            data = await self._perform(args, ctx, active)
        return ToolResult({**data, **extra})

    async def _perform(
        self, args: FilesArgs, ctx: ToolContext, active: Active
    ) -> dict[str, JsonValue]:
        workspaces = ctx.services.workspaces
        data: dict[str, JsonValue]
        try:
            match args.op:
                case "list":
                    entries = await workspaces.list_files(active, args.path)
                    data = {
                        "path": args.path,
                        "entries": [
                            {"name": e.name, "kind": e.kind, "size": e.size}
                            for e in entries[:MAX_ENTRIES]
                        ],
                        "truncated": len(entries) > MAX_ENTRIES,
                    }
                case "read":
                    head, size = await workspaces.read_file(active, args.path, args.max_chars * 4)
                    if b"\x00" in head:
                        data = {"path": args.path, "size": size, "binary": True}
                    else:
                        text = head.decode(errors="replace")
                        data = {
                            "path": args.path,
                            "size": size,
                            "text": text[: args.max_chars],
                            "truncated": len(head) < size or len(text) > args.max_chars,
                        }
                case "export":
                    artifact_id = await workspaces.export_file(active, args.path)
                    data = {"path": args.path, "artifact_id": str(artifact_id)}
                case "import":
                    data = await _import(ctx, active, args.path, args.artifact_id)
                case "write":
                    data = await _write(ctx, active, args.path, args.text)
                case "edit":
                    data = await _edit(ctx, active, args)
        except WorkspaceError as error:
            raise ToolError(error.code, error.message) from error
        return data


async def _import(
    ctx: ToolContext, active: Active, path: str, artifact_id: UUID | None
) -> dict[str, JsonValue]:
    if artifact_id is None or path == WORKSPACE_ROOT:
        raise ToolError("invalid_args", "`import` needs `artifact_id` and a file path")
    try:
        artifact, content = await ctx.services.artifacts.read(artifact_id, ctx.owner)
    except NotFoundError:
        artifact = None
        content = b""
    if artifact is None or artifact.chat_id not in {None, ctx.chat_id}:
        raise ToolError("not_found", f"artifact {artifact_id} not found")
    workspaces = ctx.services.workspaces
    await workspaces.ensure_room(active)
    await workspaces.write_file(active, path, content)
    return {"path": path, "size": len(content)}


async def _write(
    ctx: ToolContext, active: Active, path: str, text: str | None
) -> dict[str, JsonValue]:
    """Create or replace a text file."""
    if text is None or path == WORKSPACE_ROOT:
        raise ToolError("invalid_args", "`write` needs `text` and a file path")
    data = text.encode()
    await ctx.services.workspaces.ensure_room(active)
    await ctx.services.workspaces.write_file(active, path, data)
    return {"path": path, "size": len(data)}


async def _edit(ctx: ToolContext, active: Active, args: FilesArgs) -> dict[str, JsonValue]:
    """Replace a literal fragment of a text file."""
    if args.old is None or args.new is None or args.path == WORKSPACE_ROOT:
        raise ToolError("invalid_args", "`edit` needs `old`, `new` and a file path")
    workspaces = ctx.services.workspaces
    head, size = await workspaces.read_file(active, args.path, EDIT_MAX_BYTES)
    if size > EDIT_MAX_BYTES:
        raise ToolError("invalid_args", f"{args.path} is larger than {EDIT_MAX_BYTES} bytes")
    text = _text(args.path, head)
    count = text.count(args.old)
    if count == 0:
        raise ToolError("invalid_args", f"`old` was not found in {args.path}")
    if count > 1 and not args.replace_all:
        raise ToolError(
            "invalid_args",
            f"`old` occurs {count} times in {args.path}; set `replace_all` or add context",
        )
    updated = text.replace(args.old, args.new, -1 if args.replace_all else 1)
    await workspaces.ensure_room(active)
    await workspaces.write_file(active, args.path, updated.encode())
    return {"path": args.path, "replacements": count}


def _file_path(path: str) -> str | None:
    """Return the normalized path, or ``None`` when it leaves `/workspace`."""
    try:
        return workspace_path(path)
    except ResourceRejectedError:
        return None


def _text(path: str, data: bytes) -> str:
    """Return ``data`` as UTF-8 text."""
    if b"\x00" in data:
        raise ToolError("invalid_args", f"{path} is not a UTF-8 text file")
    try:
        return data.decode()
    except UnicodeDecodeError as error:
        raise ToolError("invalid_args", f"{path} is not a UTF-8 text file") from error
