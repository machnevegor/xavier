"""Control daemon of the Computer, started by one ``docker exec`` and spoken to over stdio.

Protocol: one JSON object per line. A request is ``{"id", "cmd", "args", "token"}``; the response
``{"id", "ok": true, "result"}`` or ``{"id", "ok": false, "error": {"code", "message"}}`` comes
when the command finishes, so responses may arrive out of order. ``browser`` and ``screen``
commands carry the fencing token of the GUI lease; a token lower than one already seen is
rejected. The daemon exits at the end of input, closing Chromium so that its profile is flushed.

Chromium is driven over ``--remote-debugging-pipe`` (no port), input in visual mode goes through
libxdo, screenshots are taken in-process with Pillow.
"""

import asyncio
import base64
import contextlib
import ctypes
import fcntl
import io
import json
import os
import shutil
import signal
import sys
import time
import uuid
from collections.abc import Awaitable, Callable
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from PIL import ImageDraw, ImageGrab

DISPLAY = ":0"
HOME = Path("/home/user")
PROFILE = HOME / ".config/chromium"
CACHE = HOME / ".cache/chromium"
DOWNLOADS = Path("/workspace/downloads")
LOCK = Path("/tmp/computerd.lock")
LAUNCHER = Path("/tmp/computerd.sock")
"""A connection opens a browser window: the desktop's launcher must not start another Chromium."""
X_SOCKET = Path("/tmp/.X11-unix/X0")
LIBC = ctypes.CDLL(None)
PR_SET_PDEATHSIG = 1
"""Chromium gets SIGTERM when computerd dies, so a new daemon can start it again."""
SNAPSHOT_JS = (Path(__file__).parent / "snapshot.js").read_text()
WORLD = "xavier"
CHROMIUM_FLAGS = [
    "--remote-debugging-pipe",
    f"--user-data-dir={PROFILE}",
    f"--disk-cache-dir={CACHE}",
    "--no-first-run",
    "--no-default-browser-check",
    "--password-store=basic",
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
    "--disable-features=Translate,MediaRouter",
    "--hide-crash-restore-bubble",
    "--force-device-scale-factor=1",
    "--window-position=0,0",
    "--window-size=1280,800",
    "--start-maximized",
]
CDP_TIMEOUT = 30.0
NAVIGATION_TIMEOUT = 30.0
DOWNLOAD_TIMEOUT = 120.0
SETTLE_SECONDS = 0.05
JPEG_QUALITY = 70
JOB_OUTPUT_LIMIT = 256 * 1024
KEPT_JOBS = 50
CHROMIUM_RESTART_SECONDS = 1.0

type Json = dict[str, Any]


class Fail(Exception):  # noqa: N818 - the protocol calls these failures
    """A command failure reported to the worker with a stable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def log(message: str) -> None:
    """Write a diagnostic line to stderr."""
    sys.stderr.write(f"computerd: {message}\n")
    sys.stderr.flush()


async def pipe_streams(
    read_fd: int, write_fd: int, limit: int = 64 * 1024 * 1024
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """Wrap two pipe descriptors in asyncio streams."""
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader(limit=limit)
    await loop.connect_read_pipe(
        lambda: asyncio.StreamReaderProtocol(reader), os.fdopen(read_fd, "rb", 0)
    )
    transport, protocol = await loop.connect_write_pipe(
        asyncio.streams.FlowControlMixin, os.fdopen(write_fd, "wb", 0)
    )
    return reader, asyncio.StreamWriter(transport, protocol, None, loop)


# --- CDP ------------------------------------------------------------------------------------


class Cdp:
    """One CDP connection over the Chromium debugging pipe, in flat session mode."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._reader = reader
        self._writer = writer
        self._next = 0
        self._pending: dict[int, asyncio.Future[Json]] = {}
        self._listeners: list[Callable[[str, Json, str | None], None]] = []
        self.closed = asyncio.Event()
        self._task = asyncio.create_task(self._read())

    def listen(self, callback: Callable[[str, Json, str | None], None]) -> None:
        """Call ``callback(method, params, session_id)`` for every event."""
        self._listeners.append(callback)

    async def send(
        self,
        method: str,
        params: Json | None = None,
        session: str | None = None,
        timeout: float = CDP_TIMEOUT,  # noqa: ASYNC109 - every call has its own bound
    ) -> Json:
        """Send a command and return its result."""
        if self.closed.is_set():
            raise Fail("browser_unavailable", "the browser is not running")
        self._next += 1
        message: Json = {"id": self._next, "method": method, "params": params or {}}
        if session is not None:
            message["sessionId"] = session
        future: asyncio.Future[Json] = asyncio.get_running_loop().create_future()
        self._pending[self._next] = future
        self._writer.write(json.dumps(message).encode() + b"\0")
        try:
            await self._writer.drain()
            async with asyncio.timeout(timeout):
                return await future
        except TimeoutError as error:
            raise Fail("timeout", f"the browser did not answer {method}") from error
        finally:
            self._pending.pop(message["id"], None)

    async def _read(self) -> None:
        try:
            while True:
                try:
                    raw = await self._reader.readuntil(b"\0")
                except asyncio.IncompleteReadError, ConnectionError:
                    return
                message = json.loads(raw[:-1])
                if "id" in message:
                    future = self._pending.get(message["id"])
                    if future is None or future.done():
                        continue
                    if "error" in message:
                        future.set_exception(
                            Fail("browser_error", message["error"].get("message", "CDP error"))
                        )
                    else:
                        future.set_result(message.get("result", {}))
                    continue
                for callback in list(self._listeners):
                    callback(message["method"], message.get("params", {}), message.get("sessionId"))
        finally:
            self.closed.set()
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(Fail("browser_unavailable", "the browser exited"))
            self._writer.close()


# --- Browser --------------------------------------------------------------------------------


class Page:
    """An attached tab: its CDP session and the isolated world ``snapshot.js`` runs in."""

    def __init__(self, target_id: str, session: str) -> None:
        self.target_id = target_id
        self.session = session
        self.context: tuple[str, int] | None = None
        """``(loader id, execution context id)`` of the isolated world in the current document."""
        self.next_id = 1
        """First element id ``snapshot.js`` assigns in a new document, so ids never repeat."""
        self.loaded = asyncio.Event()
        self.loaded.set()
        self.navigations = 0


class Browser:
    """Chromium: start, stop, restart on crash, tabs and the semantic actions."""

    def __init__(self) -> None:
        self._process: asyncio.subprocess.Process | None = None
        self._cdp: Cdp | None = None
        self._wanted = False
        self._targets: dict[str, Json] = {}
        self._pages: dict[str, Page] = {}
        self._current: str | None = None
        self._downloads: dict[str, Json] = {}
        self._download_events: asyncio.Queue[Json] | None = None
        self._credentials: dict[str, tuple[tuple[str, str, int], Json]] = {}
        """Origin and credential headers by CDP session of a tab (Observe ``browser`` mode)."""
        self._requests: set[asyncio.Task[None]] = set()
        self._page_seen = asyncio.Event()
        self._watch: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    @property
    def running(self) -> bool:
        """Whether Chromium runs and its pipe is open."""
        return self._cdp is not None and not self._cdp.closed.is_set()

    async def start(self) -> None:
        """Start Chromium unless it runs; it is restarted if it crashes until `stop`."""
        async with self._lock:
            self._wanted = True
            if not self.running:
                await self._launch()

    async def stop(self) -> None:
        """Close Chromium gracefully so the profile is written, then wait for it to exit."""
        async with self._lock:
            self._wanted = False
            if self._watch is not None:
                self._watch.cancel()
                self._watch = None
            process, cdp = self._process, self._cdp
            if process is None:
                return
            if cdp is not None and not cdp.closed.is_set():
                with contextlib.suppress(Fail):
                    await cdp.send("Browser.close", timeout=10)
            try:
                async with asyncio.timeout(15):
                    await process.wait()
            except TimeoutError:
                process.kill()
                await process.wait()
            self._reset()

    async def open_window(self) -> None:
        """Open a window for the operator, starting Chromium with one if it does not run."""
        if self.running:
            created = await self._require().send(
                "Target.createTarget", {"url": "about:blank", "newWindow": True}
            )
            await self._maximize([created["targetId"]])
        else:
            await self.start()

    async def _maximize(self, target_ids: list[str]) -> None:
        """Maximize the windows of the tabs: Chromium restores saved bounds that the dock covers."""
        cdp = self._require()
        for target_id in target_ids:
            window = await cdp.send("Browser.getWindowForTarget", {"targetId": target_id})
            await cdp.send(
                "Browser.setWindowBounds",
                {"windowId": window["windowId"], "bounds": {"windowState": "maximized"}},
            )

    def _reset(self) -> None:
        self._process = None
        self._cdp = None
        self._targets.clear()
        self._pages.clear()
        self._credentials.clear()
        self._current = None

    async def _launch(self) -> None:
        for directory in (PROFILE, CACHE, DOWNLOADS):
            directory.mkdir(parents=True, exist_ok=True)
        # Only this daemon runs Chromium here; locks left by a crash or restored from a
        # checkpoint of another host would make Chromium treat the profile as in use.
        for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
            (PROFILE / name).unlink(missing_ok=True)
        deadline = time.monotonic() + 10
        while not X_SOCKET.exists():
            if time.monotonic() > deadline:
                raise Fail("browser_unavailable", "the display did not start")
            await asyncio.sleep(0.05)
        to_chromium_r, to_chromium_w = os.pipe()
        from_chromium_r, from_chromium_w = os.pipe()
        # Chromium reads commands from fd 3 and writes to fd 4; lift the ends above 4 first.
        child_in = fcntl.fcntl(to_chromium_r, fcntl.F_DUPFD_CLOEXEC, 10)
        child_out = fcntl.fcntl(from_chromium_w, fcntl.F_DUPFD_CLOEXEC, 10)
        os.close(to_chromium_r)
        os.close(from_chromium_w)

        def remap() -> None:
            os.dup2(child_in, 3)
            os.dup2(child_out, 4)
            LIBC.prctl(PR_SET_PDEATHSIG, signal.SIGTERM)

        self._process = await asyncio.create_subprocess_exec(
            "chromium",
            *CHROMIUM_FLAGS,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            pass_fds=(3, 4),
            preexec_fn=remap,
            start_new_session=True,
        )
        os.close(child_in)
        os.close(child_out)
        reader, writer = await pipe_streams(from_chromium_r, to_chromium_w)
        self._page_seen = asyncio.Event()
        self._cdp = cdp = Cdp(reader, writer)
        cdp.listen(self._on_event)
        cdp.listen(self._on_request)
        self._download_events = asyncio.Queue()
        await cdp.send("Target.setDiscoverTargets", {"discover": True})
        await cdp.send(
            "Browser.setDownloadBehavior",
            {"behavior": "allowAndName", "downloadPath": str(DOWNLOADS), "eventsEnabled": True},
        )
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(10):
                await self._page_seen.wait()
        ids = self._page_ids()
        if not ids:
            ids = [(await cdp.send("Target.createTarget", {"url": "about:blank"}))["targetId"]]
        await self._maximize(ids)
        self._watch = asyncio.create_task(self._supervise(self._process))
        log("chromium started")

    async def _supervise(self, process: asyncio.subprocess.Process) -> None:
        await process.wait()
        if self._process is not process or not self._wanted:
            return
        log(f"chromium exited with {process.returncode}; restarting")
        self._reset()
        await asyncio.sleep(CHROMIUM_RESTART_SECONDS)
        async with self._lock:
            if self._wanted and not self.running:
                self._watch = None
                await self._launch()

    def _on_event(self, method: str, params: Json, session: str | None) -> None:
        if method.startswith("Page."):
            page = self._page_by_session(session)
            if page is not None:
                _on_page_event(page, method, params)
            return
        match method:
            case "Target.targetCreated" | "Target.targetInfoChanged":
                info = params["targetInfo"]
                if info["type"] == "page":
                    self._targets[info["targetId"]] = info
                    if method == "Target.targetCreated" and self._current is None:
                        self._current = info["targetId"]
                    self._page_seen.set()
            case "Target.targetDestroyed":
                target_id = params["targetId"]
                self._targets.pop(target_id, None)
                self._pages.pop(target_id, None)
                if self._current == target_id:
                    ids = self._page_ids()
                    self._current = ids[-1] if ids else None
            case "Browser.downloadWillBegin" | "Browser.downloadProgress":
                if self._download_events is not None:
                    self._download_events.put_nowait({"method": method, **params})
            case _:
                pass

    def _page_by_session(self, session: str | None) -> Page | None:
        return next((page for page in self._pages.values() if page.session == session), None)

    def _page_ids(self) -> list[str]:
        return [
            target_id
            for target_id, info in self._targets.items()
            if not info["url"].startswith("devtools://")
        ]

    def _require(self) -> Cdp:
        if self._cdp is None or self._cdp.closed.is_set():
            raise Fail("browser_unavailable", "the browser is not running; it restarts shortly")
        return self._cdp

    async def page(self) -> Page:
        """Return the current tab, attaching to it on first use."""
        cdp = self._require()
        if self._current is None or self._current not in self._targets:
            ids = self._page_ids()
            if ids:
                self._current = ids[-1]
            else:
                created = await cdp.send("Target.createTarget", {"url": "about:blank"})
                self._current = created["targetId"]
        page = self._pages.get(self._current)
        if page is None:
            attached = await cdp.send(
                "Target.attachToTarget", {"targetId": self._current, "flatten": True}
            )
            page = Page(self._current, attached["sessionId"])
            self._pages[self._current] = page
            await cdp.send("Page.enable", session=page.session)
        return page

    async def _context(self, page: Page) -> int:
        cdp = self._require()
        tree = await cdp.send("Page.getFrameTree", session=page.session)
        frame = tree["frameTree"]["frame"]
        if page.context is None or page.context[0] != frame["loaderId"]:
            world = await cdp.send(
                "Page.createIsolatedWorld",
                {"frameId": frame["id"], "worldName": WORLD},
                session=page.session,
            )
            context_id = world["executionContextId"]
            await self._evaluate(page, context_id, SNAPSHOT_JS)
            await self._evaluate(page, context_id, f"__xv.start({page.next_id})")
            page.context = (frame["loaderId"], context_id)
        return page.context[1]

    async def _evaluate(self, page: Page, context_id: int, expression: str) -> Any:  # noqa: ANN401 - JSON from the page
        result = await self._require().send(
            "Runtime.evaluate",
            {
                "expression": expression,
                "contextId": context_id,
                "returnByValue": True,
                "awaitPromise": True,
            },
            session=page.session,
        )
        if "exceptionDetails" in result:
            details = result["exceptionDetails"]
            text = details.get("exception", {}).get("description") or details.get("text")
            raise Fail("browser_error", f"page script failed: {text}")
        return result["result"].get("value")

    async def call(self, page: Page, function: str, *args: Any) -> Any:  # noqa: ANN401 - JSON from the page
        """Call ``__xv.<function>(*args)`` in the isolated world of the current document."""
        context_id = await self._context(page)
        value = await self._evaluate(
            page, context_id, f"__xv.{function}(...{json.dumps(list(args))})"
        )
        if isinstance(value, dict) and "next" in value:
            page.next_id = max(page.next_id, value["next"])
        if isinstance(value, dict) and value.get("error"):
            raise Fail(value["error"], value.get("message", value["error"]))
        return value

    def tabs(self) -> list[Json]:
        """Return the open tabs in creation order."""
        return [
            {
                "index": index,
                "title": self._targets[target_id]["title"][:120],
                "url": self._targets[target_id]["url"][:300],
                "current": target_id == self._current,
            }
            for index, target_id in enumerate(self._page_ids())
        ]

    async def snapshot(self, *, text_limit: int) -> Json:
        """Return the visible interactive elements of the current tab, its text and the tabs."""
        page = await self.page()
        result = await self.call(page, "snapshot", text_limit)
        result.pop("next", None)
        result["tabs"] = self.tabs()
        return result

    async def run(self, action: Json, *, allow_password: bool) -> Json:
        """Run one semantic action and return its result."""
        kind = action["action"]
        handler = getattr(self, f"_do_{kind}", None)
        if handler is None:
            raise Fail("invalid_args", f"unknown browser action {kind!r}")
        page = await self.page()
        before = page.navigations
        result = await handler(page, action, allow_password=allow_password)
        await self._settle(page, before)
        return result or {}

    async def _settle(self, page: Page, before: int) -> None:
        """Wait two frames or 50 ms; after a navigation started, wait for DOMContentLoaded."""
        with contextlib.suppress(Fail, TimeoutError):
            async with asyncio.timeout(SETTLE_SECONDS):
                await self.call(page, "frames")
        current = self._pages.get(page.target_id)
        if current is not None and current.navigations != before:
            with contextlib.suppress(TimeoutError):
                async with asyncio.timeout(NAVIGATION_TIMEOUT):
                    await current.loaded.wait()

    async def _point(self, page: Page, element_id: str) -> Json:
        return await self.call(page, "point", element_id)

    async def _click_at(self, page: Page, x: float, y: float, clicks: int = 1) -> None:
        cdp = self._require()
        base = {"x": x, "y": y, "button": "left"}
        await cdp.send(
            "Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y}, page.session
        )
        for count in range(1, clicks + 1):
            for kind in ("mousePressed", "mouseReleased"):
                await cdp.send(
                    "Input.dispatchMouseEvent",
                    {**base, "type": kind, "clickCount": count},
                    page.session,
                )

    async def _do_navigate(self, page: Page, action: Json, **_: object) -> Json:
        cdp = self._require()
        page.loaded.clear()
        page.navigations += 1
        result = await cdp.send("Page.navigate", {"url": action["url"]}, page.session)
        if result.get("errorText"):
            page.loaded.set()
            raise Fail("failed", f"navigation failed: {result['errorText']}")
        return {}

    async def _do_click(self, page: Page, action: Json, **_: object) -> Json:
        point = await self._point(page, action["id"])
        await self._click_at(page, point["x"], point["y"], 2 if action.get("double") else 1)
        return {}

    async def _do_type(self, page: Page, action: Json, *, allow_password: bool) -> Json:
        point = await self._point(page, action["id"])
        secret = action.get("secret")
        if secret is not None:
            self._check_secret_target(page, point, action.get("hosts", []))
        elif point.get("password") and not allow_password:
            raise Fail(
                "denied",
                "typing into a password field needs operator approval; repeat the call",
            )
        await self._click_at(page, point["x"], point["y"])
        await self.call(page, "focus", action["id"], action.get("clear", True))
        cdp = self._require()
        text = secret if secret is not None else action["text"]
        if text:
            await cdp.send("Input.insertText", {"text": text}, page.session)
        if action.get("submit"):
            await self._keys(page, "Enter")
        return {}

    def _check_secret_target(self, page: Page, point: Json, hosts: list[str]) -> None:
        """Allow a credential only into a password field of a page on one of its hosts."""
        if not point.get("password"):
            raise Fail("denied", "a credential is typed only into a password field")
        url = urlsplit(self._targets.get(page.target_id, {}).get("url", ""))
        host = (url.hostname or "").rstrip(".")
        if url.scheme != "https" or not any(fnmatchcase(host, p.lower()) for p in hosts):
            raise Fail("denied", f"the credential may not be sent to {host or 'this page'}")

    async def _do_select(self, page: Page, action: Json, **_: object) -> Json:
        return await self.call(page, "select", action["id"], action["value"])

    async def _do_press(self, page: Page, action: Json, **_: object) -> Json:
        await self._keys(page, action["key"])
        return {}

    async def _keys(self, page: Page, combo: str) -> None:
        *modifier_names, key = combo.split("+")
        modifiers = 0
        for name in modifier_names:
            bit = MODIFIERS.get(name.lower())
            if bit is None:
                raise Fail("invalid_args", f"unknown modifier {name!r}")
            modifiers |= bit
        definition = key_definition(key)
        text = definition.pop("text", None)
        cdp = self._require()
        down: Json = {"type": "keyDown", "modifiers": modifiers, **definition}
        if text is not None and not modifiers & ~MODIFIERS["shift"]:
            down["text"] = text
        await cdp.send("Input.dispatchKeyEvent", down, page.session)
        await cdp.send(
            "Input.dispatchKeyEvent",
            {"type": "keyUp", "modifiers": modifiers, **definition},
            page.session,
        )

    async def _do_scroll(self, page: Page, action: Json, **_: object) -> Json:
        if action.get("id"):
            point = await self._point(page, action["id"])
        else:
            point = await self.call(page, "center")
        amount = action.get("amount", 600)
        dx, dy = {
            "up": (0, -amount),
            "down": (0, amount),
            "left": (-amount, 0),
            "right": (amount, 0),
        }[action.get("direction", "down")]
        await self._require().send(
            "Input.dispatchMouseEvent",
            {"type": "mouseWheel", "x": point["x"], "y": point["y"], "deltaX": dx, "deltaY": dy},
            page.session,
        )
        return {}

    async def _do_wait(self, page: Page, action: Json, **_: object) -> Json:
        timeout = min(float(action.get("timeout_s", 10)), 60)
        if not action.get("text"):
            await asyncio.sleep(timeout)
            return {}
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if await self.call(page, "contains", action["text"]):
                return {"found": True}
            await asyncio.sleep(0.1)
        return {"found": False}

    async def _do_back(self, page: Page, action: Json, **_: object) -> Json:
        del action
        cdp = self._require()
        history = await cdp.send("Page.getNavigationHistory", session=page.session)
        index = history["currentIndex"]
        if index == 0:
            raise Fail("invalid_args", "there is no previous page")
        page.loaded.clear()
        page.navigations += 1
        await cdp.send(
            "Page.navigateToHistoryEntry",
            {"entryId": history["entries"][index - 1]["id"]},
            page.session,
        )
        return {}

    async def _do_tab_new(self, page: Page, action: Json, **_: object) -> Json:
        del page
        created = await self._require().send("Target.createTarget", {"url": "about:blank"})
        self._current = created["targetId"]
        new = await self.page()
        if not action.get("url"):
            return {}
        # Navigating after attaching lets the settle step see the navigation of the new tab.
        await self._do_navigate(new, action)
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(NAVIGATION_TIMEOUT):
                await new.loaded.wait()
        return {}

    async def _do_tab_switch(self, page: Page, action: Json, **_: object) -> Json:
        del page
        target_id = self._tab(action["tab"])
        await self._require().send("Target.activateTarget", {"targetId": target_id})
        self._current = target_id
        return {}

    async def _do_tab_close(self, page: Page, action: Json, **_: object) -> Json:
        target_id = self._tab(action["tab"]) if action.get("tab") is not None else page.target_id
        await self._require().send("Target.closeTarget", {"targetId": target_id})
        return {}

    def _tab(self, index: int) -> str:
        ids = self._page_ids()
        if not 0 <= index < len(ids):
            raise Fail("invalid_args", f"no tab {index}; open tabs are 0..{len(ids) - 1}")
        return ids[index]

    async def _do_extract(self, page: Page, action: Json, **_: object) -> Json:
        return await self.call(page, "extract", action.get("id"), action.get("max_chars", 20000))

    async def _do_headers(self, page: Page, action: Json, **_: object) -> Json:
        """Send extra headers with every request of the tab (Observe ``browser`` mode)."""
        cdp = self._require()
        await cdp.send("Network.enable", session=page.session)
        await cdp.send("Network.setExtraHTTPHeaders", {"headers": action["headers"]}, page.session)
        return {}

    async def _do_credential(self, page: Page, action: Json, **_: object) -> Json:
        """Send credential headers only with the tab's requests to the origin of ``url``."""
        if not action["headers"]:
            return {}
        self._credentials[page.session] = (_origin(action["url"]), action["headers"])
        await self._require().send(
            "Fetch.enable",
            {"patterns": [{"urlPattern": "*", "requestStage": "Request"}]},
            page.session,
        )
        return {}

    def _on_request(self, method: str, params: Json, session: str | None) -> None:
        if method == "Fetch.requestPaused":
            task = asyncio.create_task(self._continue_request(params, session))
            self._requests.add(task)
            task.add_done_callback(self._requests.discard)

    async def _continue_request(self, params: Json, session: str | None) -> None:
        request = params["request"]
        continued: Json = {"requestId": params["requestId"]}
        scoped = self._credentials.get(session) if session is not None else None
        if scoped is not None and _origin(request["url"]) == scoped[0]:
            names = {name.lower() for name in scoped[1]}
            kept = {k: v for k, v in request["headers"].items() if k.lower() not in names}
            headers = {**kept, **scoped[1]}
            continued["headers"] = [{"name": k, "value": v} for k, v in headers.items()]
        with contextlib.suppress(Fail):
            await self._require().send("Fetch.continueRequest", continued, session)

    async def _do_html(self, page: Page, action: Json, **_: object) -> Json:
        """Return the rendered document as HTML (Observe ``browser`` mode)."""
        return await self.call(page, "html", action.get("max_chars", 5 * 1024 * 1024))

    async def _do_download(self, page: Page, action: Json, **_: object) -> Json:
        events = self._download_events
        if events is None:
            raise Fail("browser_unavailable", "the browser is not running")
        while not events.empty():
            events.get_nowait()
        if action.get("url"):
            with contextlib.suppress(Fail):
                await self._require().send("Page.navigate", {"url": action["url"]}, page.session)
        else:
            point = await self._point(page, action["id"])
            await self._click_at(page, point["x"], point["y"])
        names: dict[str, str] = {}
        try:
            async with asyncio.timeout(DOWNLOAD_TIMEOUT):
                while True:
                    event = await events.get()
                    if event["method"] == "Browser.downloadWillBegin":
                        names[event["guid"]] = event["suggestedFilename"]
                    elif event["guid"] in names and event["state"] == "canceled":
                        raise Fail("failed", "the download was cancelled")
                    elif event["guid"] in names and event["state"] == "completed":
                        path = place_download(event["guid"], names[event["guid"]])
                        return {"path": str(path), "size": path.stat().st_size}
        except TimeoutError as error:
            raise Fail("timeout", "no download finished in time") from error


def _origin(url: str) -> tuple[str, str, int]:
    """Return the scheme, lowercase host and port of ``url``."""
    parts = urlsplit(url)
    return (
        parts.scheme,
        parts.hostname or "",
        parts.port or (443 if parts.scheme == "https" else 80),
    )


def _on_page_event(page: Page, method: str, params: Json) -> None:
    """Track main-frame navigations of a tab."""
    main_frame = params.get("frameId") == page.target_id
    match method:
        case "Page.frameStartedLoading" if main_frame:
            page.navigations += 1
            page.loaded.clear()
        case "Page.domContentEventFired":
            page.loaded.set()
        case "Page.frameStoppedLoading" if main_frame:
            # Downloads and failed navigations stop without DOMContentLoaded.
            page.loaded.set()
        case _:
            pass


def place_download(guid: str, suggested: str) -> Path:
    """Move a finished download from its GUID name to a free name based on the suggested one."""
    name = Path(suggested).name or "download"
    stem, suffix = Path(name).stem, Path(name).suffix
    target = DOWNLOADS / name
    counter = 1
    while target.exists():
        target = DOWNLOADS / f"{stem} ({counter}){suffix}"
        counter += 1
    shutil.move(DOWNLOADS / guid, target)
    return target


MODIFIERS = {"alt": 1, "control": 2, "ctrl": 2, "meta": 4, "shift": 8}
_NAMED_KEYS: dict[str, tuple[str, int, str | None]] = {
    "Enter": ("Enter", 13, "\r"),
    "Tab": ("Tab", 9, None),
    "Escape": ("Escape", 27, None),
    "Backspace": ("Backspace", 8, None),
    "Delete": ("Delete", 46, None),
    "ArrowLeft": ("ArrowLeft", 37, None),
    "ArrowUp": ("ArrowUp", 38, None),
    "ArrowRight": ("ArrowRight", 39, None),
    "ArrowDown": ("ArrowDown", 40, None),
    "Home": ("Home", 36, None),
    "End": ("End", 35, None),
    "PageUp": ("PageUp", 33, None),
    "PageDown": ("PageDown", 34, None),
    "Space": ("Space", 32, " "),
    "F5": ("F5", 116, None),
}


def key_definition(key: str) -> Json:
    """Return the CDP key event fields of a key name or a single character."""
    if key in _NAMED_KEYS:
        code, virtual, text = _NAMED_KEYS[key]
        found: Json = {"key": key if key != "Space" else " ", "code": code}
        found["windowsVirtualKeyCode"] = virtual
        if text is not None:
            found["text"] = text
        return found
    if len(key) == 1:
        upper = key.upper()
        if upper.isalpha():
            code = f"Key{upper}"
        elif key.isdigit():
            code = f"Digit{key}"
        else:
            code = ""
        return {"key": key, "code": code, "windowsVirtualKeyCode": ord(upper), "text": key}
    raise Fail("invalid_args", f"unknown key {key!r}")


# --- Visual mode ----------------------------------------------------------------------------


class Xdo:
    """Mouse and keyboard input through libxdo, without a process per action."""

    CURRENT_WINDOW = 0
    _DELAY_US = 12000

    def __init__(self, display: str) -> None:
        lib = ctypes.CDLL("libxdo.so.3")
        lib.xdo_new.restype = ctypes.c_void_p
        lib.xdo_new.argtypes = [ctypes.c_char_p]
        window = ctypes.c_ulong
        signatures: dict[str, list[Any]] = {
            "xdo_move_mouse": [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int],
            "xdo_click_window": [ctypes.c_void_p, window, ctypes.c_int],
            "xdo_click_window_multiple": [
                ctypes.c_void_p,
                window,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_uint,
            ],
            "xdo_mouse_down": [ctypes.c_void_p, window, ctypes.c_int],
            "xdo_mouse_up": [ctypes.c_void_p, window, ctypes.c_int],
            "xdo_enter_text_window": [ctypes.c_void_p, window, ctypes.c_char_p, ctypes.c_uint],
            "xdo_send_keysequence_window": [
                ctypes.c_void_p,
                window,
                ctypes.c_char_p,
                ctypes.c_uint,
            ],
            "xdo_get_active_window": [ctypes.c_void_p, ctypes.POINTER(window)],
            "xdo_activate_window": [ctypes.c_void_p, window],
            "xdo_get_window_name": [
                ctypes.c_void_p,
                window,
                ctypes.POINTER(ctypes.c_char_p),
                ctypes.POINTER(ctypes.c_int),
                ctypes.POINTER(ctypes.c_int),
            ],
        }
        for name, argtypes in signatures.items():
            function = getattr(lib, name)
            function.argtypes = argtypes
            function.restype = ctypes.c_int
        self._lib = lib
        self._xdo = lib.xdo_new(display.encode())
        if not self._xdo:
            raise RuntimeError(f"libxdo cannot open display {display}")
        self._x11 = X11(display)

    def _check(self, name: str, *args: object) -> None:
        if getattr(self._lib, name)(self._xdo, *args) != 0:
            raise Fail("failed", f"{name} failed")

    def move(self, x: int, y: int) -> None:
        """Move the pointer."""
        self._check("xdo_move_mouse", x, y, 0)

    def click(self, x: int, y: int, button: int = 1, repeat: int = 1) -> None:
        """Click ``repeat`` times at a point."""
        self.move(x, y)
        self._check(
            "xdo_click_window_multiple", self.CURRENT_WINDOW, button, repeat, self._DELAY_US * 8
        )

    def drag(self, start: tuple[int, int], end: tuple[int, int]) -> None:
        """Press at ``start``, move to ``end`` and release."""
        self.move(*start)
        self._check("xdo_mouse_down", self.CURRENT_WINDOW, 1)
        steps = 10
        for step in range(1, steps + 1):
            self.move(
                start[0] + (end[0] - start[0]) * step // steps,
                start[1] + (end[1] - start[1]) * step // steps,
            )
            time.sleep(0.01)
        self._check("xdo_mouse_up", self.CURRENT_WINDOW, 1)

    def type(self, text: str) -> None:
        """Type text into the focused window."""
        self._check("xdo_enter_text_window", self.CURRENT_WINDOW, text.encode(), self._DELAY_US)

    def key(self, sequence: str) -> None:
        """Press a key sequence in xdotool syntax, such as ``ctrl+l``."""
        self._check(
            "xdo_send_keysequence_window", self.CURRENT_WINDOW, sequence.encode(), self._DELAY_US
        )

    def scroll(self, x: int, y: int, direction: str, amount: int) -> None:
        """Turn the wheel ``amount`` notches at a point."""
        button = {"up": 4, "down": 5, "left": 6, "right": 7}[direction]
        self.click(x, y, button, amount)

    def windows(self) -> list[Json]:
        """Return the managed top-level windows."""
        active = ctypes.c_ulong()
        self._lib.xdo_get_active_window(self._xdo, ctypes.byref(active))
        found = []
        for window in self._x11.client_windows():
            name = ctypes.c_char_p()
            length, kind = ctypes.c_int(), ctypes.c_int()
            self._lib.xdo_get_window_name(
                self._xdo, window, ctypes.byref(name), ctypes.byref(length), ctypes.byref(kind)
            )
            title = (name.value or b"")[: length.value].decode(errors="replace")
            if name.value is not None:
                self._x11.free(name)
            found.append({"id": window, "title": title[:120], "active": window == active.value})
        return found

    def focus(self, window: int) -> None:
        """Activate a window."""
        if window not in self._x11.client_windows():
            raise Fail("invalid_args", f"no window {window}")
        self._check("xdo_activate_window", window)


class X11:
    """The few Xlib calls libxdo does not cover: the window manager's client list."""

    def __init__(self, display: str) -> None:
        lib = ctypes.CDLL("libX11.so.6")
        lib.XOpenDisplay.restype = ctypes.c_void_p
        lib.XOpenDisplay.argtypes = [ctypes.c_char_p]
        lib.XDefaultRootWindow.restype = ctypes.c_ulong
        lib.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        lib.XInternAtom.restype = ctypes.c_ulong
        lib.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
        lib.XGetWindowProperty.argtypes = [
            ctypes.c_void_p,
            ctypes.c_ulong,
            ctypes.c_ulong,
            ctypes.c_long,
            ctypes.c_long,
            ctypes.c_int,
            ctypes.c_ulong,
            ctypes.POINTER(ctypes.c_ulong),
            ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_ulong),
            ctypes.POINTER(ctypes.c_ulong),
            ctypes.POINTER(ctypes.POINTER(ctypes.c_ulong)),
        ]
        lib.XFree.argtypes = [ctypes.c_void_p]
        self._lib = lib
        self._display = lib.XOpenDisplay(display.encode())
        if not self._display:
            raise RuntimeError(f"cannot open display {display}")
        self._root = lib.XDefaultRootWindow(self._display)
        self._clients = lib.XInternAtom(self._display, b"_NET_CLIENT_LIST", 0)

    def free(self, pointer: ctypes.c_char_p) -> None:
        """Release memory Xlib allocated."""
        self._lib.XFree(pointer)

    def client_windows(self) -> list[int]:
        """Return ``_NET_CLIENT_LIST`` of the root window."""
        kind, size_format = ctypes.c_ulong(), ctypes.c_int()
        count, remaining = ctypes.c_ulong(), ctypes.c_ulong()
        data = ctypes.POINTER(ctypes.c_ulong)()
        any_property_type = 0
        status = self._lib.XGetWindowProperty(
            self._display,
            self._root,
            self._clients,
            0,
            1024,
            0,
            any_property_type,
            ctypes.byref(kind),
            ctypes.byref(size_format),
            ctypes.byref(count),
            ctypes.byref(remaining),
            ctypes.byref(data),
        )
        if status != 0 or not data:
            return []
        try:
            return [int(data[index]) for index in range(count.value)]
        finally:
            self._lib.XFree(data)


class Screen:
    """Screenshots and visual-mode input."""

    def __init__(self, browser: Browser) -> None:
        self._browser = browser
        self._xdo: Xdo | None = None

    @property
    def xdo(self) -> Xdo:
        """The libxdo adapter, created on first use."""
        if self._xdo is None:
            self._xdo = Xdo(DISPLAY)
        return self._xdo

    async def screenshot(self, *, marks: bool) -> Json:
        """Return a JPEG of the screen, with element marks of the current tab on request."""
        elements = []
        if marks and self._browser.running:
            snapshot = await self._browser.snapshot(text_limit=0)
            elements = snapshot["elements"]
        return await asyncio.to_thread(self._render, elements)

    def _render(self, elements: list[Json]) -> Json:
        image = ImageGrab.grab(xdisplay=DISPLAY).convert("RGB")
        found = []
        if elements:
            draw = ImageDraw.Draw(image)
            for element in elements:
                box = element.get("screen")
                if box is None:
                    continue
                x, y, width, height = box
                draw.rectangle((x, y, x + width, y + height), outline=(255, 0, 64), width=2)
                draw.rectangle((x, y - 12, x + 8 * len(element["id"]) + 4, y), fill=(255, 0, 64))
                draw.text((x + 2, y - 12), element["id"], fill=(255, 255, 255))
                found.append(
                    {
                        "id": element["id"],
                        "role": element["role"],
                        "name": element.get("name", "")[:60],
                        "x": x + width // 2,
                        "y": y + height // 2,
                    }
                )
        buffer = io.BytesIO()
        image.save(buffer, "JPEG", quality=JPEG_QUALITY)
        return {
            "mime_type": "image/jpeg",
            "data": base64.b64encode(buffer.getvalue()).decode(),
            "width": image.width,
            "height": image.height,
            "marks": found,
        }

    async def act(self, args: Json) -> Json:
        """Run a visual-mode input action."""
        action = args["action"]
        xdo = self.xdo
        match action:
            case "click" | "double_click":
                repeat = 2 if action == "double_click" else 1
                button = {"left": 1, "middle": 2, "right": 3}[args.get("button", "left")]
                await asyncio.to_thread(xdo.click, args["x"], args["y"], button, repeat)
            case "move":
                await asyncio.to_thread(xdo.move, args["x"], args["y"])
            case "drag":
                await asyncio.to_thread(
                    xdo.drag, (args["x"], args["y"]), (args["to_x"], args["to_y"])
                )
            case "type":
                await asyncio.to_thread(xdo.type, args["text"])
            case "key":
                await asyncio.to_thread(xdo.key, args["keys"])
            case "scroll":
                await asyncio.to_thread(
                    xdo.scroll,
                    args["x"],
                    args["y"],
                    args.get("direction", "down"),
                    args.get("amount", 5),
                )
            case "windows":
                return {"windows": await asyncio.to_thread(xdo.windows)}
            case "focus_window":
                await asyncio.to_thread(xdo.focus, args["window"])
            case _:
                raise Fail("invalid_args", f"unknown screen action {action!r}")
        await asyncio.sleep(SETTLE_SECONDS)
        return {}


# --- Terminal -------------------------------------------------------------------------------


class Job:
    """A terminal command with a ring buffer of its combined output."""

    def __init__(self, process: asyncio.subprocess.Process) -> None:
        self.id = str(uuid.uuid7())
        self.process = process
        self.output = bytearray()
        self.dropped = 0
        self.reader = asyncio.create_task(self._read())

    async def _read(self) -> None:
        stdout = self.process.stdout
        if stdout is None:
            raise RuntimeError("jobs start with a stdout pipe")
        while chunk := await stdout.read(65536):
            self.output.extend(chunk)
            excess = len(self.output) - JOB_OUTPUT_LIMIT
            if excess > 0:
                del self.output[:excess]
                self.dropped += excess

    @property
    def done(self) -> bool:
        """Whether the command exited and its output was read."""
        return self.process.returncode is not None and self.reader.done()

    def view(self) -> Json:
        """Return the job status and the kept output."""
        return {
            "job_id": self.id,
            "job_status": "finished" if self.done else "running",
            "exit_code": self.process.returncode if self.done else None,
            "output": self.output.decode(errors="replace"),
            "truncated": self.dropped > 0,
        }


class Terminal:
    """Commands in the Computer; one still running after ``wait_s`` becomes a job."""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}

    async def run(self, command: str, cwd: str, wait_s: float) -> Json:
        """Start ``command`` with bash and wait up to ``wait_s`` for it."""
        if not Path(cwd).is_dir():
            raise Fail("invalid_args", f"{cwd} is not a directory")
        process = await asyncio.create_subprocess_exec(
            "bash",
            "-lc",
            command,
            cwd=cwd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
        job = Job(process)
        self._jobs[job.id] = job
        while len(self._jobs) > KEPT_JOBS:
            oldest = next((key for key, value in self._jobs.items() if value.done), None)
            if oldest is None:
                break
            del self._jobs[oldest]
        return await self.wait(job.id, wait_s)

    async def wait(self, job_id: str, wait_s: float) -> Json:
        """Wait up to ``wait_s`` for a job and return its view."""
        job = self._job(job_id)
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(wait_s):
                await job.process.wait()
                await job.reader
        return job.view()

    async def cancel(self, job_id: str) -> Json:
        """Terminate a job's process group, killing it after 5 s."""
        job = self._job(job_id)
        if job.process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(job.process.pid, signal.SIGTERM)
            try:
                async with asyncio.timeout(5):
                    await job.process.wait()
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(job.process.pid, signal.SIGKILL)
                await job.process.wait()
        await job.reader
        return job.view()

    def running(self) -> int:
        """Return the number of running jobs."""
        return sum(1 for job in self._jobs.values() if not job.done)

    def kill_all(self) -> None:
        """Kill every running job; jobs do not outlive the daemon that reports them."""
        for job in self._jobs.values():
            if job.process.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(job.process.pid, signal.SIGKILL)

    def _job(self, job_id: str) -> Job:
        job = self._jobs.get(job_id)
        if job is None:
            raise Fail("not_found", f"no terminal job {job_id}")
        return job


# --- Daemon ---------------------------------------------------------------------------------


class Daemon:
    """Dispatches requests; ``browser`` and ``screen`` run one at a time under the fence."""

    GUI = frozenset({"browser", "screen"})

    def __init__(self, writer: asyncio.StreamWriter) -> None:
        self._writer = writer
        self.browser = Browser()
        self.screen = Screen(self.browser)
        self.terminal = Terminal()
        self._fence = 0
        self._gui = asyncio.Lock()
        self._commands: dict[str, Callable[[Json], Awaitable[Json]]] = {
            "ping": self._ping,
            "fence": self._set_fence,
            "browser.start": self._browser_start,
            "browser.stop": self._browser_stop,
            "browser": self._browser,
            "screen": self._screen,
            "screenshot": self._screenshot,
            "terminal.run": self._terminal_run,
            "job.wait": self._job_wait,
            "job.cancel": self._job_cancel,
        }

    async def handle(self, line: bytes) -> None:
        """Run one request and write its response."""
        request_id = None
        try:
            request = json.loads(line)
            request_id = request["id"]
            command = self._command(request["cmd"])
            args = request.get("args") or {}
            if request["cmd"] in self.GUI:
                async with self._gui:
                    self._check_fence(request.get("token"))
                    result = await command(args)
            else:
                result = await command(args)
            response: Json = {"id": request_id, "ok": True, "result": result}
        except Fail as error:
            response = {
                "id": request_id,
                "ok": False,
                "error": {"code": error.code, "message": error.message},
            }
        except Exception as error:  # noqa: BLE001 - every failure must reach the worker
            log(f"command failed: {error!r}")
            response = {
                "id": request_id,
                "ok": False,
                "error": {"code": "failed", "message": f"{type(error).__name__}: {error}"},
            }
        self._writer.write(json.dumps(response).encode() + b"\n")
        await self._writer.drain()

    def _command(self, name: str) -> Callable[[Json], Awaitable[Json]]:
        command = self._commands.get(name)
        if command is None:
            raise Fail("invalid_args", f"unknown command {name!r}")
        return command

    def _check_fence(self, token: object) -> None:
        if not isinstance(token, int):
            raise Fail("invalid_args", "GUI commands need the lease token")
        if token < self._fence:
            raise Fail("stale_token", "a newer holder of the GUI lease exists")
        self._fence = token

    async def _ping(self, args: Json) -> Json:
        del args
        return {"browser": self.browser.running, "jobs": self.terminal.running()}

    async def _set_fence(self, args: Json) -> Json:
        self._fence = max(self._fence, int(args["token"]))
        return {"fence": self._fence}

    async def _browser_start(self, args: Json) -> Json:
        del args
        await self.browser.start()
        return {}

    async def _browser_stop(self, args: Json) -> Json:
        del args
        await self.browser.stop()
        return {}

    async def _browser(self, args: Json) -> Json:
        results: list[Json] = []
        error: Json | None = None
        for action in args.get("actions", []):
            try:
                result = await self.browser.run(
                    action, allow_password=bool(args.get("allow_password"))
                )
            except Fail as failure:
                error = {"code": failure.code, "message": failure.message}
                results.append({"action": action["action"], "ok": False, "error": error})
                break
            results.append({"action": action["action"], "ok": True, **result})
        response: Json = {"results": results}
        if args.get("snapshot", True):
            response["snapshot"] = await self.browser.snapshot(
                text_limit=int(args.get("text_limit", 3000))
            )
        return response

    async def _screen(self, args: Json) -> Json:
        if args["action"] == "screenshot":
            return await self.screen.screenshot(marks=bool(args.get("marks")))
        return await self.screen.act(args)

    async def _screenshot(self, args: Json) -> Json:
        """Take a screenshot for the operator; reading the screen needs no lease."""
        del args
        return await self.screen.screenshot(marks=False)

    async def _terminal_run(self, args: Json) -> Json:
        return await self.terminal.run(
            args["command"], args.get("cwd", "/workspace"), args["wait_s"]
        )

    async def _job_wait(self, args: Json) -> Json:
        return await self.terminal.wait(args["job_id"], args["wait_s"])

    async def _job_cancel(self, args: Json) -> Json:
        return await self.terminal.cancel(args["job_id"])


async def main() -> None:
    """Serve requests until the end of input or SIGTERM."""
    lock = LOCK.open("w")
    # A previous daemon of this container exits when its channel closes; wait for it.
    await asyncio.to_thread(fcntl.flock, lock, fcntl.LOCK_EX)
    reader, writer = await pipe_streams(os.dup(sys.stdin.fileno()), os.dup(sys.stdout.fileno()))
    daemon = Daemon(writer)
    stop = asyncio.Event()
    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, stop.set)
    tasks: set[asyncio.Task[None]] = set()

    async def open_window(_reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writer.close()
        try:
            await daemon.browser.open_window()
        except Fail as failure:
            log(f"cannot open a browser window: {failure.message}")

    LAUNCHER.unlink(missing_ok=True)
    launcher = await asyncio.start_unix_server(open_window, path=LAUNCHER)

    async def serve() -> None:
        while line := await reader.readline():
            task = asyncio.create_task(daemon.handle(line))
            tasks.add(task)
            task.add_done_callback(tasks.discard)

    server = asyncio.create_task(serve())
    stopped = asyncio.create_task(stop.wait())
    await asyncio.wait({server, stopped}, return_when=asyncio.FIRST_COMPLETED)
    for task in (server, stopped, *tasks):
        task.cancel()
    launcher.close()
    daemon.terminal.kill_all()
    await daemon.browser.stop()
    log("stopped")


if __name__ == "__main__":
    asyncio.run(main())
