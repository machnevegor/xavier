import asyncio
import base64
import contextlib
import json
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from pydantic import JsonValue, ValidationError

from src.compute import service as compute_service
from src.compute.backend import ComputerSpec
from src.compute.channel import Channel, ChannelClosedError, ComputerError, Transport
from src.compute.docker_backend import computer_config, seccomp_profile
from src.compute.service import ComputeService
from src.config import ComputerSettings
from src.db.models import Artifact, Execution, ExecutionKind, ExecutionStatus
from src.execute.service import Executions
from src.leases import USER, Held, Leases
from src.policy.model import Mode, ResourceKind, Risk
from src.policy.urls import ResourceRejectedError
from src.storage.artifacts import Artifacts
from src.tools.base import ToolContext, ToolError
from src.tools.compute import (
    REDACTED,
    Browser,
    BrowserAction,
    BrowserArgs,
    Screen,
    ScreenArgs,
    render_snapshot,
)
from src.tools.jobs import Job, JobArgs
from src.workspace.service import Active, WorkspaceService
from tests.fakes import services as tool_services

CHAT = uuid.uuid4()


class FakeTransport:
    def __init__(self) -> None:
        self.incoming: asyncio.Queue[bytes | None] = asyncio.Queue()
        self.requests: asyncio.Queue[dict[str, JsonValue]] = asyncio.Queue()

    async def read(self) -> bytes | None:
        return await self.incoming.get()

    async def write(self, data: bytes) -> None:
        for line in data.splitlines():
            self.requests.put_nowait(json.loads(line))

    async def close(self) -> None:
        self.incoming.put_nowait(None)

    def reply(self, request: dict[str, JsonValue], **response: JsonValue) -> None:
        line = json.dumps({"id": request["id"], **response}).encode() + b"\n"
        # Lines may arrive split across reads.
        self.incoming.put_nowait(line[:7])
        self.incoming.put_nowait(line[7:])


async def test_channel_matches_responses_to_requests_in_any_order() -> None:
    transport = FakeTransport()
    channel = Channel(transport)

    first = asyncio.create_task(channel.call("ping"))
    second = asyncio.create_task(channel.call("browser", {"actions": []}, token=3))
    ping, browser = await transport.requests.get(), await transport.requests.get()
    transport.incoming.put_nowait(b"not json\n")
    transport.reply(browser, ok=True, result={"n": 2})
    transport.reply(ping, ok=True, result={"n": 1})

    assert await first == {"n": 1}
    assert await second == {"n": 2}
    assert browser == {"id": browser["id"], "cmd": "browser", "args": {"actions": []}, "token": 3}
    await channel.close()


async def test_channel_raises_the_reported_error_code() -> None:
    transport = FakeTransport()
    channel = Channel(transport)

    call = asyncio.create_task(channel.call("screen", {"action": "click"}, token=1))
    transport.reply(
        await transport.requests.get(),
        ok=False,
        error={"code": "stale_token", "message": "newer holder"},
    )

    with pytest.raises(ComputerError) as raised:
        await call
    assert (raised.value.code, raised.value.message) == ("stale_token", "newer holder")
    await channel.close()


async def test_closed_channel_fails_pending_and_new_calls_as_env_lost() -> None:
    transport = FakeTransport()
    channel = Channel(transport)

    pending = asyncio.create_task(channel.call("terminal.run"))
    await transport.requests.get()
    transport.incoming.put_nowait(None)

    with pytest.raises(ChannelClosedError) as raised:
        await pending
    assert raised.value.code == "env_lost"
    assert channel.closed
    with pytest.raises(ChannelClosedError):
        await channel.call("ping")


class FakeLeases(Leases):
    """One lease with the semantics of the SQL implementation."""

    def __init__(self) -> None:
        self.holder: str | None = None
        self.token = 0

    async def acquire(self, key: str, holder: str, ttl: timedelta) -> int | None:
        if self.holder is not None:
            return None
        self.holder, self.token = holder, self.token + 1
        return self.token

    async def current(self, key: str) -> Held | None:
        if self.holder is None:
            return None
        return Held(self.holder, self.token, datetime.now(UTC) + timedelta(minutes=1))

    async def release(self, key: str, holder: str, token: int) -> None:
        if (self.holder, self.token) == (holder, token):
            self.holder = None


async def test_gui_calls_of_a_chat_run_one_at_a_time_with_growing_tokens() -> None:
    compute = ComputeService(FakeLeases())
    inside, peak, tokens = 0, 0, []

    async def call(number: int) -> None:
        nonlocal inside, peak
        async with compute.gui(CHAT, f"action:{number}") as token:
            inside += 1
            peak = max(peak, inside)
            tokens.append(token)
            await asyncio.sleep(0.01)
            inside -= 1

    await asyncio.gather(*(call(number) for number in range(3)))

    assert peak == 1
    assert tokens == [1, 2, 3]


async def test_gui_is_busy_while_the_user_controls_it() -> None:
    leases = FakeLeases()
    leases.holder = USER

    with pytest.raises(ComputerError) as raised:
        async with ComputeService(leases).gui(CHAT, "action:a"):
            pass
    assert raised.value.code == "env_busy"


async def test_gui_waits_for_another_holder_then_gives_up(monkeypatch: pytest.MonkeyPatch) -> None:
    leases = FakeLeases()
    leases.holder = "action:other"
    compute = ComputeService(leases)

    async def free_soon() -> None:
        await asyncio.sleep(0.05)
        leases.holder = None

    freed = asyncio.create_task(free_soon())
    async with compute.gui(CHAT, "action:a") as token:
        assert token == 1
    await freed

    leases.holder = "action:other"
    monkeypatch.setattr(compute_service, "GUI_WAIT_SECONDS", 0.05)
    with pytest.raises(ComputerError) as raised:
        async with compute.gui(CHAT, "action:b"):
            pass
    assert raised.value.code == "env_busy"


class FakeComputer:
    id = "computer-1"

    def __init__(self, results: dict[str, dict[str, JsonValue] | ComputerError]) -> None:
        self.results = results
        self.calls: list[tuple[str, dict[str, JsonValue] | None, int | None]] = []

    async def call(
        self,
        command: str,
        args: dict[str, JsonValue] | None = None,
        *,
        token: int | None = None,
        timeout: float = 60,  # noqa: ASYNC109 - mirrors the protocol
    ) -> dict[str, JsonValue]:
        del timeout
        self.calls.append((command, args, token))
        result = self.results[command]
        if isinstance(result, ComputerError):
            raise result
        return result

    @contextlib.asynccontextmanager
    async def vnc(self, *, control: bool) -> AsyncGenerator[Transport]:
        raise NotImplementedError
        yield FakeTransport()

    async def destroy(self) -> None:
        pass


class FakeWorkspaces(WorkspaceService):
    def __init__(self, computer: FakeComputer) -> None:
        self.computer = computer

    async def activate(self, chat_id: UUID, *, computer: bool = False) -> Active:
        return Active(
            chat_id, 1, "ws", "home", "holder", computer=self.computer if computer else None
        )

    async def touch(self, chat_id: UUID) -> None:
        del chat_id

    async def running_computer(self, chat_id: UUID) -> FakeComputer | None:
        del chat_id
        return self.computer


class FakeArtifacts(Artifacts):
    def __init__(self) -> None:
        self.created: list[tuple[str, bytes]] = []

    async def create(  # noqa: PLR0913 - mirrors Artifacts.create
        self,
        *,
        chat_id: UUID | None,
        name: str,
        content_type: str,
        data: bytes,
        source: str,
        created_by: UUID | None = None,
    ) -> Artifact:
        self.created.append((source, data))
        return Artifact(id=uuid.uuid7(), chat_id=chat_id, name=name, size_bytes=len(data))


async def _public(host: str, port: int) -> list[str]:
    del host, port
    return ["93.184.215.14"]


async def _private(host: str, port: int) -> list[str]:
    del host, port
    return ["10.0.0.7"]


def _context(  # noqa: PLR0913 - optional collaborators for tool contexts
    computer: FakeComputer,
    *,
    risk: Risk | None = Risk.WRITE,
    image_input: bool = True,
    compute: ComputeService | None = None,
    artifacts: FakeArtifacts | None = None,
    executions: Executions | None = None,
) -> ToolContext:
    return ToolContext(
        chat_id=CHAT,
        owner=uuid.uuid7(),
        action_id=uuid.uuid7(),
        mode=Mode.AUTONOMOUS,
        execute_network=False,
        image_input=image_input,
        services=tool_services(
            artifacts=artifacts or FakeArtifacts(),
            workspaces=FakeWorkspaces(computer),
            executions=executions,
            compute=compute or ComputeService(FakeLeases(), _public),
        ),
        risk=risk,
    )


SNAPSHOT: dict[str, JsonValue] = {
    "url": "https://example.com/login",
    "title": "Sign in",
    "elements": [
        {"id": "e1", "role": "textbox", "name": "Email", "value": "a@b.c", "screen": [0, 0, 1, 1]},
        {"id": "e2", "role": "textbox", "name": "Password", "password": True},
        {"id": "e3", "role": "checkbox", "name": "Remember", "states": ["checked"]},
    ],
    "truncated": False,
    "text": "Sign in to continue",
    "scroll": {"y": 0, "height": 900, "viewport": 800},
    "tabs": [{"index": 0, "title": "Sign in", "url": "https://example.com/login", "current": True}],
}


def test_snapshot_renders_one_line_per_element() -> None:
    rendered = render_snapshot(SNAPSHOT)

    assert rendered["elements"] == (
        'e1 textbox "Email" value="a@b.c"\n'
        'e2 textbox "Password" password\n'
        'e3 checkbox "Remember" [checked]'
    )
    assert rendered["tabs"] == "0* Sign in - https://example.com/login"
    assert rendered["scroll"] == "at 0 of 900 px, viewport 800"
    assert "elements_truncated" not in rendered


def test_browser_actions_need_their_arguments() -> None:
    for action in (
        {"action": "navigate"},
        {"action": "type", "id": "e1"},
        {"action": "download"},
        {"action": "download", "id": "e1", "url": "https://example.com/a"},
        {"action": "click", "id": "button"},
    ):
        with pytest.raises(ValidationError):
            BrowserAction.model_validate(action)


def test_browser_urls_are_normalized_and_become_resources() -> None:
    tool = Browser()
    args = tool.prepare(
        BrowserArgs(actions=[BrowserAction(action="navigate", url="HTTPS://Example.COM/a#top")])
    )

    assert args.actions[0].url == "https://example.com/a"
    assert [(r.kind, r.value) for r in tool.resources(args)] == [
        (ResourceKind.URL, "https://example.com/a"),
        (ResourceKind.HOST, "example.com"),
    ]


def test_browser_opens_workspace_files_as_path_resources() -> None:
    tool = Browser()
    args = tool.prepare(
        BrowserArgs(
            actions=[
                BrowserAction(action="navigate", url="FILE:///workspace/a/../game%20one.html?l=2"),
                BrowserAction(action="tab_new", url="file:///workspace/b.html"),
            ]
        )
    )

    assert [action.url for action in args.actions] == [
        "file:///workspace/game%20one.html?l=2",
        "file:///workspace/b.html",
    ]
    assert [(r.kind, r.value) for r in tool.resources(args)] == [
        (ResourceKind.PATH, "/workspace/game one.html"),
        (ResourceKind.PATH, "/workspace/b.html"),
    ]


@pytest.mark.parametrize(
    ("action", "url"),
    [
        ("navigate", "file:///etc/hostname"),
        ("navigate", "file:///workspace/%2e%2e/etc/hostname"),
        ("navigate", "file://host/workspace/a.html"),
        ("download", "file:///workspace/a.html"),
    ],
)
def test_browser_refuses_file_urls_outside_the_workspace(action: str, url: str) -> None:
    args = BrowserArgs.model_validate({"actions": [{"action": action, "url": url}]})

    with pytest.raises(ResourceRejectedError):
        Browser().prepare(args)


async def test_browser_opens_a_workspace_file_without_an_address_check() -> None:
    computer = FakeComputer({"browser": {"results": [], "snapshot": SNAPSHOT}})
    ctx = _context(computer, compute=ComputeService(FakeLeases(), _private))
    tool = Browser()
    args = tool.prepare(
        BrowserArgs(actions=[BrowserAction(action="navigate", url="file:///workspace/a.html")])
    )

    await tool.run(args, ctx)

    [(_command, request, _token)] = computer.calls
    assert request is not None
    steps = request["actions"]
    assert isinstance(steps, list)
    [step] = steps
    assert isinstance(step, dict)
    assert step["url"] == "file:///workspace/a.html"


async def test_browser_runs_the_batch_under_the_lease_and_renders_the_snapshot() -> None:
    computer = FakeComputer(
        {"browser": {"results": [{"action": "navigate", "ok": True}], "snapshot": SNAPSHOT}}
    )
    ctx = _context(computer)
    args = BrowserArgs(actions=[BrowserAction(action="navigate", url="https://example.com/login")])

    result = await Browser().run(args, ctx)

    [(command, request, token)] = computer.calls
    assert (command, token) == ("browser", 1)
    assert request is not None
    assert request["allow_password"] is False
    assert result.data["results"] == [{"action": "navigate", "ok": True}]
    snapshot = result.data["snapshot"]
    assert isinstance(snapshot, dict)
    assert snapshot["title"] == "Sign in"


async def test_typing_into_a_password_field_is_sensitive_and_hidden() -> None:
    computer = FakeComputer({"browser": {"results": [], "snapshot": SNAPSHOT}})
    ctx = _context(computer)
    tool = Browser()
    await tool.run(BrowserArgs(), ctx)
    typing = BrowserArgs(
        actions=[
            BrowserAction(action="type", id="e1", text="a@b.c"),
            BrowserAction(action="type", id="e2", text="hunter2"),
        ]
    )

    assert await tool.classify(typing, ctx) is Risk.SENSITIVE
    assert await tool.classify(BrowserArgs(actions=[typing.actions[0]]), ctx) is Risk.WRITE
    shown = tool.redact(typing.model_dump(mode="json"), ctx)["actions"]
    assert isinstance(shown, list)
    assert [action["text"] for action in shown if isinstance(action, dict)] == ["a@b.c", REDACTED]

    await tool.run(typing, _context(computer, risk=Risk.SENSITIVE, compute=ctx.services.compute))
    request = computer.calls[-1][1]
    assert request is not None
    assert request["allow_password"] is True


async def test_browser_refuses_internal_addresses_before_touching_the_computer() -> None:
    computer = FakeComputer({})
    ctx = _context(computer, compute=ComputeService(FakeLeases(), _private))
    args = BrowserArgs(actions=[BrowserAction(action="navigate", url="https://intranet.example")])

    with pytest.raises(ToolError) as raised:
        await Browser().run(args, ctx)
    assert raised.value.code == "denied"
    assert computer.calls == []


async def test_a_stale_token_reaches_the_model_as_env_busy() -> None:
    computer = FakeComputer({"browser": ComputerError("stale_token", "newer holder")})

    with pytest.raises(ToolError) as raised:
        await Browser().run(BrowserArgs(), _context(computer))
    assert raised.value.code == "env_busy"


async def test_screenshot_needs_image_input() -> None:
    computer = FakeComputer({})

    with pytest.raises(ToolError) as raised:
        await Screen().run(ScreenArgs(action="screenshot"), _context(computer, image_input=False))
    assert raised.value.code == "invalid_args"
    assert computer.calls == []


async def test_screenshot_is_an_image_and_an_artifact() -> None:
    jpeg = base64.b64encode(b"\xff\xd8jpeg").decode()
    shot: dict[str, JsonValue] = {"data": jpeg, "width": 1280, "height": 800, "marks": []}
    computer = FakeComputer({"screen": shot})
    artifacts = FakeArtifacts()
    tool = Screen()
    args = ScreenArgs(action="screenshot", marks=True)

    result = await tool.run(args, _context(computer, artifacts=artifacts))

    assert await tool.classify(args, _context(computer)) is Risk.READ
    assert (
        await tool.classify(ScreenArgs(action="click", x=1, y=2), _context(computer)) is Risk.WRITE
    )
    assert [image.data for image in result.images] == [jpeg]
    assert artifacts.created == [("screenshot", b"\xff\xd8jpeg")]
    assert computer.calls[0][1] == {
        "action": "screenshot",
        "marks": True,
        "button": "left",
        "direction": "down",
        "amount": 5,
    }


async def test_job_reads_a_finished_terminal_execution() -> None:
    job_id = uuid.uuid7()

    class Done(Executions):
        def __init__(self) -> None:
            pass

        async def get(self, execution_id: UUID) -> Execution:
            assert execution_id == job_id
            return Execution(
                id=job_id,
                created_by=uuid.uuid7(),
                chat_id=CHAT,
                kind=ExecutionKind.TERMINAL,
                status=ExecutionStatus.SUCCEEDED,
                language="bash",
                command="echo ok",
                network=True,
                export=[],
                input_artifact_ids=[],
                stdout="ok\n",
                stderr="",
                truncated=False,
                output_artifact_ids=[],
                metrics={},
                exit_code=0,
                error=None,
            )

        async def until_done(
            self, execution_ids: object, within: float, *, first: bool = False
        ) -> None:
            del execution_ids, within, first

        async def collect(self, execution_ids: object) -> None:
            del execution_ids

    computer = FakeComputer({})
    result = await Job().run(
        JobArgs(job_id=job_id, wait_s=5), _context(computer, executions=Done())
    )

    assert result.data["job_status"] == "succeeded"
    assert result.data["stdout"] == "ok\n"
    assert computer.calls == []


def test_computer_container_is_hardened() -> None:
    config = computer_config(ComputerSettings(), ComputerSpec("ws", "home", {"a": "b"}))
    host = config["HostConfig"]

    assert isinstance(host, dict)
    assert host["CapDrop"] == ["ALL"]
    assert host["ReadonlyRootfs"] is True
    assert "Privileged" not in host
    assert host["NetworkMode"] == "xv-sandbox"
    security = host["SecurityOpt"]
    assert isinstance(security, list)
    assert security[0] == "no-new-privileges:true"
    assert config["User"] != "root"


def test_seccomp_profile_is_the_default_plus_the_chromium_sandbox_calls() -> None:
    profile = json.loads(seccomp_profile())

    assert profile["defaultAction"] == "SCMP_ACT_ERRNO"
    assert profile["syscalls"][-1] == {
        "names": ["chroot", "clone", "unshare"],
        "action": "SCMP_ACT_ALLOW",
    }
    allowed = {
        name
        for rule in profile["syscalls"]
        if rule["action"] == "SCMP_ACT_ALLOW" and not rule.get("includes")
        for name in rule["names"]
    }
    assert not allowed & {"mount", "ptrace", "bpf", "kexec_load", "init_module"}
