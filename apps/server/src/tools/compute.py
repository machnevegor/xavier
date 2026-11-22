"""Compute tools: the browser, the screen, the terminal and the health of the chat's Computer."""

import base64
from typing import Literal, Self
from urllib.parse import quote, unquote, urlsplit
from uuid import UUID

from pydantic import Field, JsonValue, model_validator

from src.compute.backend import Computer
from src.compute.channel import ComputerError
from src.credentials.service import CredentialDeniedError
from src.db.models import ToolGroup
from src.policy.commands import analyze
from src.policy.model import Resource, ResourceKind, Risk
from src.policy.urls import ResourceRejectedError, normalize_url, workspace_path
from src.tools.base import (
    Image,
    Tool,
    ToolArgs,
    ToolContext,
    ToolError,
    ToolResult,
    use_workspace,
    workspace_changes,
)
from src.tools.execute import wait_for_jobs
from src.workspace.service import WorkspaceError

MAX_ACTIONS = 20
SNAPSHOT_TEXT_CHARS = 3000
BROWSER_TIMEOUT = 240.0
"""Bound of one `browser` batch; below the gateway handler deadline."""
SCREEN_TIMEOUT = 60.0
MAX_WAIT_SECONDS = 110
TERMINAL_FIRST_WAIT = 3
"""Seconds a command runs before it is tracked as a job that new input can stop waiting for."""
REDACTED = "[redacted]"
_PASSED_CODES = frozenset({"invalid_args", "denied", "not_found", "timeout", "env_busy"})


def tool_error(error: ComputerError) -> ToolError:
    """Return the tool error for a failure inside the Computer."""
    if error.code == "stale_token":
        return ToolError("env_busy", "another holder took the Computer; try again")
    return ToolError(error.code if error.code in _PASSED_CODES else "failed", error.message)


async def computer_of(ctx: ToolContext) -> tuple[Computer, dict[str, JsonValue]]:
    """Return the chat's Computer, activating the workspace with it, and the extra fields."""
    active, extra = await use_workspace(ctx, computer=True)
    if active.computer is None:
        raise TypeError("an activation with a Computer returns it")
    return active.computer, extra


class BrowserAction(ToolArgs):
    """One step of a `browser` batch."""

    action: Literal[
        "navigate",
        "click",
        "type",
        "select",
        "press",
        "scroll",
        "wait",
        "back",
        "tab_new",
        "tab_switch",
        "tab_close",
        "extract",
        "download",
    ] = Field(description="What to do.")
    id: str | None = Field(
        default=None,
        pattern=r"^e\d+$",
        description=(
            "Element id from the latest snapshot, such as `e12`: required for `click`, `type` "
            "and `select`; optional for `scroll`, `extract` and `download`."
        ),
    )
    url: str | None = Field(
        default=None,
        description=(
            "http or https URL for `navigate`, `tab_new` or `download`; `navigate` and "
            "`tab_new` also open files as `file:///workspace/...`."
        ),
    )
    text: str | None = Field(
        default=None,
        max_length=20_000,
        description="Text to `type`, or text `wait` waits to appear on the page.",
    )
    credential_id: UUID | None = Field(
        default=None,
        description=(
            "`type`: a `login` credential with `agent` from the `capabilities` notice instead "
            "of `text`. Its password is typed only into a password field of an https page on "
            "the credential's hosts; you never see it. Type its `username` as plain `text`."
        ),
    )
    submit: bool = Field(default=False, description="`type`: press Enter afterwards.")
    clear: bool = Field(default=True, description="`type`: replace the current value.")
    value: str | None = Field(default=None, description="`select`: option value or label.")
    key: str | None = Field(
        default=None,
        description="`press`: a key such as `Enter`, `Escape`, `ArrowDown` or `Control+a`.",
    )
    direction: Literal["up", "down", "left", "right"] = Field(
        default="down", description="`scroll` direction."
    )
    amount: int = Field(default=600, ge=1, le=10_000, description="`scroll` distance in pixels.")
    tab: int | None = Field(
        default=None, ge=0, description="Tab index for `tab_switch` and `tab_close`."
    )
    double: bool = Field(default=False, description="`click`: double-click.")
    timeout_s: float = Field(default=10, gt=0, le=60, description="`wait`: seconds to wait.")
    max_chars: int = Field(
        default=20_000, ge=1, le=100_000, description="`extract`: characters to return."
    )

    @model_validator(mode="after")
    def _required(self) -> Self:
        needed: dict[str, tuple[str, ...]] = {
            "navigate": ("url",),
            "click": ("id",),
            "type": ("id",),
            "select": ("id", "value"),
            "press": ("key",),
            "tab_switch": ("tab",),
        }
        missing = [name for name in needed.get(self.action, ()) if getattr(self, name) is None]
        if missing:
            raise ValueError(f"`{self.action}` needs {', '.join(f'`{m}`' for m in missing)}")
        if self.action == "type" and (self.text is None) == (self.credential_id is None):
            raise ValueError("`type` needs exactly one of `text` and `credential_id`")
        if self.action != "type" and self.credential_id is not None:
            raise ValueError("only `type` takes `credential_id`")
        if self.action == "download" and (self.id is None) == (self.url is None):
            raise ValueError("`download` needs exactly one of `id` and `url`")
        return self


class BrowserArgs(ToolArgs):
    """Arguments of `browser`."""

    actions: list[BrowserAction] = Field(
        default=[],
        max_length=MAX_ACTIONS,
        description="Steps run in order; the batch stops at the first failed step.",
    )
    snapshot: bool = Field(
        default=True, description="Return a snapshot of the page after the steps."
    )

    def urls(self) -> list[str]:
        """Return the URLs the steps open."""
        return [action.url for action in self.actions if action.url is not None]


def _target(action: BrowserAction, url: str) -> tuple[str, list[Resource]]:
    """Return the normalized URL a step opens and its resources.

    The Chromium policy of the Computer blocks ``file:`` URLs outside ``/workspace``.
    """
    parts = urlsplit(url.strip())
    if parts.scheme.lower() != "file":
        normalized, host, _port = normalize_url(url)
        return normalized, [
            Resource(ResourceKind.URL, normalized),
            Resource(ResourceKind.HOST, host),
        ]
    if action.action not in {"navigate", "tab_new"}:
        raise ResourceRejectedError(f"`{action.action}` needs an http or https URL")
    if parts.netloc:
        raise ResourceRejectedError("file URLs cannot name a host")
    path = workspace_path(unquote(parts.path))
    query = f"?{parts.query}" if parts.query else ""
    return f"file://{quote(path)}{query}", [Resource(ResourceKind.PATH, path)]


def render_snapshot(snapshot: dict[str, JsonValue]) -> dict[str, JsonValue]:
    """Return the compact form of a page snapshot the model reads.

    Elements become one line each: ``e12 button "Sign in" value="..." password [checked]``.
    """
    lines = []
    for element in _list(snapshot.get("elements")):
        if not isinstance(element, dict):
            continue
        line = f'{element["id"]} {element["role"]} "{element.get("name", "")}"'
        if "value" in element:
            line += f' value="{element["value"]}"'
        if element.get("password"):
            line += " password"
        states = _list(element.get("states"))
        if states:
            line += f" [{', '.join(str(state) for state in states)}]"
        lines.append(line)
    tabs = [
        f"{tab['index']}{'*' if tab.get('current') else ''} {tab['title']} - {tab['url']}"
        for tab in _list(snapshot.get("tabs"))
        if isinstance(tab, dict)
    ]
    rendered: dict[str, JsonValue] = {
        "url": snapshot.get("url"),
        "title": snapshot.get("title"),
        "tabs": "\n".join(tabs),
        "elements": "\n".join(lines),
        "text": snapshot.get("text"),
    }
    if snapshot.get("truncated"):
        rendered["elements_truncated"] = True
    scroll = snapshot.get("scroll")
    if isinstance(scroll, dict):
        rendered["scroll"] = (
            f"at {scroll.get('y')} of {scroll.get('height')} px, viewport {scroll.get('viewport')}"
        )
    return rendered


def _list(value: JsonValue) -> list[JsonValue]:
    return value if isinstance(value, list) else []


class Browser(Tool[BrowserArgs]):
    """Semantic browsing in the chat's Chromium."""

    name = "browser"
    groups = frozenset({ToolGroup.COMPUTE})
    description = (
        "Use the Chromium browser of the chat's Computer. Runs a batch of steps (navigate, "
        "click, type, select, press, scroll, wait, back, tabs, extract, download) and returns "
        "each step's result plus a snapshot of the page: visible interactive elements with "
        "ids such as `e12`, the visible text and the tabs. Refer to elements by the ids of the "
        "latest snapshot; batch several steps when you know them. Downloads go to "
        "/workspace/downloads. Prefer this over `screen`; use `screen` for canvas, iframes "
        "and applications outside the browser."
    )
    consequences = (
        "Acts in the chat's browser like a person: it can follow links, submit forms with "
        "the typed values and download files into /workspace."
    )
    risk = Risk.WRITE
    args_type = BrowserArgs

    def prepare(self, args: BrowserArgs) -> BrowserArgs:
        """Normalize the URLs."""
        actions = [
            action.model_copy(update={"url": _target(action, action.url)[0]})
            if action.url is not None
            else action
            for action in args.actions
        ]
        return args.model_copy(update={"actions": actions})

    def resources(self, args: BrowserArgs) -> list[Resource]:
        """Return the URLs with their hosts and the workspace paths of ``file:`` URLs."""
        return [
            resource
            for action in args.actions
            if action.url is not None
            for resource in _target(action, action.url)[1]
        ]

    async def classify(self, args: BrowserArgs, ctx: ToolContext) -> Risk:
        """Rate typing text into a password field of the last snapshot as ``sensitive``.

        A credential is bound to its hosts by the operator, so typing it stays ``write``.
        """
        passwords = ctx.services.compute.password_fields(ctx.chat_id)
        if any(
            a.action == "type" and a.text is not None and a.id in passwords for a in args.actions
        ):
            return Risk.SENSITIVE
        return Risk.WRITE

    def redact(self, arguments: dict[str, JsonValue], ctx: ToolContext) -> dict[str, JsonValue]:
        """Hide text typed into password fields."""
        passwords = ctx.services.compute.password_fields(ctx.chat_id)
        actions = [
            {**action, "text": REDACTED}
            if isinstance(action, dict)
            and action.get("action") == "type"
            and action.get("id") in passwords
            else action
            for action in _list(arguments.get("actions"))
        ]
        return {**arguments, "actions": actions}

    async def run(self, args: BrowserArgs, ctx: ToolContext) -> ToolResult:
        """Run the batch under the GUI lease."""
        compute = ctx.services.compute
        for url in args.urls():
            if url.startswith("file:"):
                continue
            try:
                await compute.check_url(url)
            except ResourceRejectedError as error:
                raise ToolError("denied", str(error)) from error
        computer, extra = await computer_of(ctx)
        request: dict[str, JsonValue] = {
            "actions": [await _step(action, ctx) for action in args.actions],
            "snapshot": args.snapshot,
            "text_limit": SNAPSHOT_TEXT_CHARS,
            "allow_password": ctx.risk is Risk.SENSITIVE,
        }
        try:
            async with compute.gui(ctx.chat_id, f"action:{ctx.action_id}") as token:
                result = await computer.call(
                    "browser", request, token=token, timeout=BROWSER_TIMEOUT
                )
        except ComputerError as error:
            raise tool_error(error) from error
        data: dict[str, JsonValue] = {"results": result.get("results", [])}
        snapshot = result.get("snapshot")
        if isinstance(snapshot, dict):
            compute.remember(ctx.chat_id, snapshot)
            data["snapshot"] = render_snapshot(snapshot)
        return ToolResult({**data, **extra})


async def _step(action: BrowserAction, ctx: ToolContext) -> dict[str, JsonValue]:
    """Return the step as the Computer runs it; a credential becomes its value and hosts."""
    step: dict[str, JsonValue] = action.model_dump(mode="json", exclude_none=True)
    if action.credential_id is None:
        return step
    try:
        secret, hosts = await ctx.services.credentials.for_browser(
            action.credential_id, chat_id=ctx.chat_id, owner=ctx.owner, action_id=ctx.action_id
        )
    except CredentialDeniedError as error:
        raise ToolError("denied", str(error)) from error
    del step["credential_id"]
    return {**step, "secret": secret, "hosts": list(hosts)}


class ScreenArgs(ToolArgs):
    """Arguments of `screen`."""

    action: Literal[
        "screenshot",
        "click",
        "double_click",
        "move",
        "drag",
        "type",
        "key",
        "scroll",
        "windows",
        "focus_window",
    ] = Field(description="What to do.")
    marks: bool = Field(
        default=False,
        description="`screenshot`: outline the browser's interactive elements with their ids.",
    )
    x: int | None = Field(default=None, ge=0, le=10_000, description="Screen x in pixels.")
    y: int | None = Field(default=None, ge=0, le=10_000, description="Screen y in pixels.")
    to_x: int | None = Field(default=None, ge=0, le=10_000, description="`drag` target x.")
    to_y: int | None = Field(default=None, ge=0, le=10_000, description="`drag` target y.")
    button: Literal["left", "middle", "right"] = Field(default="left", description="`click`.")
    text: str | None = Field(default=None, max_length=20_000, description="`type`: text.")
    keys: str | None = Field(
        default=None, description="`key`: xdotool key sequence such as `ctrl+l` or `alt+F4`."
    )
    direction: Literal["up", "down", "left", "right"] = Field(
        default="down", description="`scroll` direction."
    )
    amount: int = Field(default=5, ge=1, le=50, description="`scroll`: wheel notches.")
    window: int | None = Field(default=None, description="`focus_window`: id from `windows`.")

    @model_validator(mode="after")
    def _required(self) -> Self:
        needed: dict[str, tuple[str, ...]] = {
            "click": ("x", "y"),
            "double_click": ("x", "y"),
            "move": ("x", "y"),
            "drag": ("x", "y", "to_x", "to_y"),
            "type": ("text",),
            "key": ("keys",),
            "scroll": ("x", "y"),
            "focus_window": ("window",),
        }
        missing = [name for name in needed.get(self.action, ()) if getattr(self, name) is None]
        if missing:
            raise ValueError(f"`{self.action}` needs {', '.join(f'`{m}`' for m in missing)}")
        return self


class Screen(Tool[ScreenArgs]):
    """Visual control of the chat's desktop."""

    name = "screen"
    groups = frozenset({ToolGroup.COMPUTE})
    description = (
        "See and control the desktop of the chat's Computer (1280x800). `screenshot` returns "
        "a JPEG, with element ids of the browser page when `marks` is true; the other actions "
        "use the mouse and keyboard at screen coordinates and manage windows. Screenshots "
        "need a model with image input. Use `browser` for web pages when it can do the job."
    )
    consequences = "Moves the mouse and types on the chat's desktop, which acts on any window."
    risk = Risk.WRITE
    args_type = ScreenArgs

    async def classify(self, args: ScreenArgs, ctx: ToolContext) -> Risk:
        """Rate looking as ``read`` and input as ``write``."""
        del ctx
        return Risk.READ if args.action in {"screenshot", "windows"} else Risk.WRITE

    async def run(self, args: ScreenArgs, ctx: ToolContext) -> ToolResult:
        """Run the action under the GUI lease."""
        if args.action == "screenshot" and not ctx.image_input:
            raise ToolError(
                "invalid_args",
                "the chat model does not accept images; use `browser` snapshots instead",
            )
        computer, extra = await computer_of(ctx)
        try:
            async with ctx.services.compute.gui(ctx.chat_id, f"action:{ctx.action_id}") as token:
                result = await computer.call(
                    "screen",
                    args.model_dump(mode="json", exclude_none=True),
                    token=token,
                    timeout=SCREEN_TIMEOUT,
                )
        except ComputerError as error:
            raise tool_error(error) from error
        if args.action != "screenshot":
            return ToolResult({**result, **extra})
        data = str(result["data"])
        artifact = await ctx.services.artifacts.create(
            chat_id=ctx.chat_id,
            name=f"screenshot-{ctx.action_id}.jpg",
            content_type="image/jpeg",
            data=base64.b64decode(data),
            source="screenshot",
        )
        return ToolResult(
            {
                "width": result["width"],
                "height": result["height"],
                "marks": result["marks"],
                "artifact_id": str(artifact.id),
                **extra,
            },
            images=[Image(mime_type="image/jpeg", data=data)],
        )


class TerminalArgs(ToolArgs):
    """Arguments of `terminal`."""

    command: str = Field(max_length=10_000, description="A bash command line.")
    cwd: str = Field(default="/workspace", pattern=r"^/", description="Absolute working directory.")
    wait_s: int = Field(
        default=60,
        ge=0,
        le=MAX_WAIT_SECONDS,
        description="Seconds to wait for the result; a longer command returns a `job_id`.",
    )


class Terminal(Tool[TerminalArgs]):
    """A shell command in the chat's Computer."""

    name = "terminal"
    groups = frozenset({ToolGroup.COMPUTE})
    description = (
        "Run a bash command in the chat's Computer, next to its browser and desktop, as the "
        "user that owns /workspace and the home directory. A command still running after "
        "`wait_s` returns a `job_id` for `job`. Programs with windows appear on the screen. "
        "Processes do not survive hibernation of the workspace; files in /workspace do."
    )
    consequences = "Runs the command in the Computer; it can change files in /workspace and home."
    risk = Risk.WRITE
    args_type = TerminalArgs

    async def classify(self, args: TerminalArgs, ctx: ToolContext) -> Risk:
        """Rate a line of only reading operations ``read``, anything else ``write``."""
        del ctx
        return Risk.READ if analyze(args.command, args.cwd).read_only else Risk.WRITE

    def resources(self, args: TerminalArgs) -> list[Resource]:
        """Return the operations of the command line."""
        operations = analyze(args.command, args.cwd).operations
        return [Resource(ResourceKind.COMMAND, operation) for operation in operations]

    async def run(self, args: TerminalArgs, ctx: ToolContext) -> ToolResult:
        """Start the command; one still running after a moment becomes a tracked job."""
        computer, extra = await computer_of(ctx)
        first_wait = min(args.wait_s, TERMINAL_FIRST_WAIT)
        try:
            result = await computer.call(
                "terminal.run",
                {"command": args.command, "cwd": args.cwd, "wait_s": first_wait},
                timeout=first_wait + SCREEN_TIMEOUT,
            )
        except ComputerError as error:
            raise tool_error(error) from error
        if result.get("job_status") != "running":
            return ToolResult({**result, **extra})
        job_id = UUID(str(result["job_id"]))
        await ctx.services.executions.track(
            job_id,
            chat_id=ctx.chat_id,
            action_id=ctx.action_id,
            command=args.command,
            task_queue=ctx.services.host_queue,
        )
        [job] = await wait_for_jobs(ctx, [job_id], args.wait_s - first_wait)
        return ToolResult({**job, **extra})


class ComputerArgs(ToolArgs):
    """Arguments of `computer`."""

    action: Literal["status", "restart"]


class ComputerTool(Tool[ComputerArgs]):
    """Health of the chat's Computer and its restart."""

    name = "computer"
    groups = frozenset({ToolGroup.COMPUTE})
    description = (
        "`status` reports the chat's Computer and workspace: CPU time, memory against its "
        "limit, processes, whether the browser runs and how many terminal jobs, disk use of "
        "/workspace and home against its limit, where the workspace is and its last checkpoint, "
        "container health (state, out-of-memory kill, restarts) and recent failures. `restart` "
        "replaces a Computer that hangs or ran out of memory. Limits are set by the operator; "
        "when the work needs more, tell the user."
    )
    consequences = (
        "Restarting ends every process and terminal job in the Computer and closes the browser; "
        "/workspace and the home directory are kept."
    )
    risk = Risk.WRITE
    args_type = ComputerArgs

    async def classify(self, args: ComputerArgs, ctx: ToolContext) -> Risk:
        """Rate `status` ``read`` and `restart` ``write``."""
        del ctx
        return Risk.READ if args.action == "status" else Risk.WRITE

    async def run(self, args: ComputerArgs, ctx: ToolContext) -> ToolResult:
        """Report the status, or restart the Computer under the GUI lease."""
        workspaces = ctx.services.workspaces
        if args.action == "status":
            return ToolResult(await workspaces.status(ctx.chat_id))
        try:
            async with ctx.services.compute.gui(ctx.chat_id, f"action:{ctx.action_id}"):
                active = await workspaces.restart_computer(ctx.chat_id)
        except WorkspaceError as error:
            raise ToolError(error.code, error.message) from error
        except ComputerError as error:
            raise tool_error(error) from error
        await workspaces.touch(ctx.chat_id)
        return ToolResult({"restarted": True, **workspace_changes(active)})
