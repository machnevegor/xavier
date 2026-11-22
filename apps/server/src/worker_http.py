"""Worker internal server for ``api``: files and the Computer of workspaces active here."""

import asyncio
import base64
import contextlib
import hmac
from uuid import UUID

from starlette.applications import Starlette
from starlette.datastructures import Headers
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from src.compute.channel import ComputerError, Transport
from src.config import Settings
from src.db.models import WorkspaceStatus
from src.leases import USER, Leases, gui_key
from src.policy.urls import WORKSPACE_ROOT, ResourceRejectedError, workspace_path
from src.workspace.service import MAX_FILE_BYTES, Active, WorkspaceError, WorkspaceService

WORKSPACE_HIBERNATED = "workspace_hibernated"
"""Detail of a 409: the workspace is not active on this worker."""
CONTROL_NOT_HELD = "control_not_held"
"""Close reason of a control stream without the user lease."""
CLOSE_UNAUTHORIZED = 4401
CLOSE_CONFLICT = 4409
"""Close code of a stream refused for the state of the workspace or lease; ``api`` passes it on."""
_STATUS = {"not_found": 404, "invalid_args": 413}


class _Handlers:
    """Handlers; every request needs ``worker.internal_token``."""

    def __init__(self, settings: Settings, workspaces: WorkspaceService, leases: Leases) -> None:
        self._expected = f"Bearer {settings.worker.internal_token.get_secret_value()}".encode()
        self._host = settings.worker.id
        self._workspaces = workspaces
        self._leases = leases

    async def list(self, request: Request) -> Response:
        workspace, path = await self._target(request)
        try:
            entries = await self._workspaces.list_files(workspace, path)
        except WorkspaceError as error:
            raise _http(error) from error
        return JSONResponse(
            {
                "path": path,
                "entries": [
                    {"name": e.name, "kind": e.kind, "size": e.size, "modified": e.modified}
                    for e in entries
                ],
            }
        )

    async def content(self, request: Request) -> Response:
        workspace, path = await self._target(request)
        try:
            _head, size = await self._workspaces.read_file(workspace, path, 0)
        except WorkspaceError as error:
            raise _http(error) from error
        if size > MAX_FILE_BYTES:
            raise HTTPException(413, f"files over {MAX_FILE_BYTES} bytes are not served")
        return StreamingResponse(
            self._workspaces.stream_file(workspace, path),
            media_type="application/octet-stream",
            headers={"content-length": str(size)},
        )

    async def screenshot(self, request: Request) -> Response:
        if not self._authorized(request.headers):
            raise HTTPException(401)
        active = await self._computer(UUID(request.path_params["chat_id"]))
        if active is None or active.computer is None:
            raise HTTPException(409, WORKSPACE_HIBERNATED)
        try:
            shot = await active.computer.call("screenshot")
        except ComputerError as error:
            raise HTTPException(503, error.message) from error
        return Response(base64.b64decode(str(shot["data"])), media_type="image/jpeg")

    async def status(self, request: Request) -> Response:
        if not self._authorized(request.headers):
            raise HTTPException(401)
        return JSONResponse(await self._workspaces.status(UUID(request.path_params["chat_id"])))

    async def vnc(self, websocket: WebSocket) -> None:
        """Relay RFB between ``api`` and x11vnc; ``control=1`` needs the user lease ``token``."""
        if not self._authorized(websocket.headers):
            await websocket.close(CLOSE_UNAUTHORIZED)
            return
        await websocket.accept()
        chat_id = UUID(websocket.path_params["chat_id"])
        control = websocket.query_params.get("control") == "1"
        token = int(websocket.query_params.get("token", "0"))
        if control:
            held = await self._leases.current(gui_key(chat_id))
            if held is None or held.holder != USER or held.token != token:
                await websocket.close(CLOSE_CONFLICT, CONTROL_NOT_HELD)
                return
        active = await self._computer(chat_id)
        if active is None or active.computer is None:
            await websocket.close(CLOSE_CONFLICT, WORKSPACE_HIBERNATED)
            return
        computer = active.computer
        if control:
            await computer.call("fence", {"token": token})
        await self._workspaces.touch(chat_id)
        try:
            async with computer.vnc(control=control) as stream:
                await relay(websocket, stream)
        finally:
            await self._workspaces.touch(chat_id)
            with contextlib.suppress(RuntimeError, WebSocketDisconnect):
                await websocket.close()

    async def _computer(self, chat_id: UUID) -> Active | None:
        """Return a workspace active here with its Computer started, or ``None``."""
        row = await self._workspaces.get(chat_id)
        if row is None or row.status is not WorkspaceStatus.ACTIVE or row.host != self._host:
            return None
        try:
            return await self._workspaces.activate(chat_id, computer=True)
        except WorkspaceError:
            return None

    async def _target(self, request: Request) -> tuple[Active, str]:
        if not self._authorized(request.headers):
            raise HTTPException(401)
        chat_id = UUID(request.path_params["chat_id"])
        row = await self._workspaces.get(chat_id)
        if row is None or row.status is not WorkspaceStatus.ACTIVE or row.host != self._host:
            raise HTTPException(409, WORKSPACE_HIBERNATED)
        try:
            path = workspace_path(request.query_params.get("path", WORKSPACE_ROOT))
        except ResourceRejectedError as error:
            raise HTTPException(400, str(error)) from error
        try:
            return await self._workspaces.activate(chat_id), path
        except WorkspaceError as error:
            raise HTTPException(409, WORKSPACE_HIBERNATED) from error

    def _authorized(self, headers: Headers) -> bool:
        presented = headers.get("authorization", "").encode()
        return hmac.compare_digest(presented, self._expected)


async def relay(websocket: WebSocket, stream: Transport) -> None:
    """Copy binary messages to ``stream`` and its output back until either side ends."""

    async def upstream() -> None:
        with contextlib.suppress(WebSocketDisconnect):
            while True:
                await stream.write(await websocket.receive_bytes())

    async def downstream() -> None:
        with contextlib.suppress(WebSocketDisconnect):
            while (chunk := await stream.read()) is not None:
                await websocket.send_bytes(chunk)

    tasks = [asyncio.create_task(upstream()), asyncio.create_task(downstream())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    for task in tasks:
        if not task.cancelled() and (error := task.exception()) is not None:
            raise error


def _http(error: WorkspaceError) -> HTTPException:
    return HTTPException(_STATUS.get(error.code, 500), error.message)


async def _error(request: Request, exc: Exception) -> Response:
    del request
    if not isinstance(exc, HTTPException):
        raise exc
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


def internal_app(settings: Settings, workspaces: WorkspaceService, leases: Leases) -> Starlette:
    """Return the internal application."""
    handlers = _Handlers(settings, workspaces, leases)
    return Starlette(
        routes=[
            Route("/internal/workspaces/{chat_id}/files", handlers.list),
            Route("/internal/workspaces/{chat_id}/content", handlers.content),
            Route("/internal/workspaces/{chat_id}/screenshot", handlers.screenshot),
            Route("/internal/workspaces/{chat_id}/status", handlers.status),
            WebSocketRoute("/internal/workspaces/{chat_id}/vnc", handlers.vnc),
        ],
        exception_handlers={HTTPException: _error},
    )
