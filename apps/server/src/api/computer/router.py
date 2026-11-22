"""The chat's Computer: screenshot, status, control by the user and the screen stream.

The Computer is the desktop of the chat workspace; it runs while the workspace is active.
While the user has control, the agent's `browser` and `screen` calls fail with `env_busy` and
the workspace does not hibernate. Control lasts `computer.control_ttl` and is kept while a
control screen stream is open; closing that stream returns it.
"""

import asyncio
import contextlib
from collections.abc import Awaitable
from datetime import timedelta
from typing import Annotated
from uuid import UUID

from fastapi import Query, Response, WebSocket, WebSocketDisconnect
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake

from src.api.common import CONFLICT, UNAVAILABLE, router
from src.api.computer.schemas import (
    ComputerStatus,
    Control,
    ControlAction,
    ReadComputerStatusResult,
    UpdateControlArgs,
    UpdateControlResult,
)
from src.api.dependencies import (
    STREAM_CHECK_S,
    OwnedChatDep,
    Services,
    ServicesDep,
    SessionDep,
    authenticate,
    still_active,
)
from src.api.workspace.service import call_worker, worker_headers, worker_url
from src.auth.service import Principal
from src.db import models as db
from src.errors import ComputerBusyError, UnavailableError, WorkspaceHibernatedError, XavierError
from src.leases import USER, gui_key
from src.workspace.status import lifecycle, recent_failures

router = router("/chats/{chat_id}/computer", "computer")

CLOSE_UNAUTHORIZED = 4401
CLOSE_NOT_FOUND = 4404
CLOSE_CONFLICT = 4409
CLOSE_UNAVAILABLE = 4503
CONTROL_NOT_HELD = "control_not_held"
_APPLICATION_CODES = range(4000, 5000)
"""Close codes of the worker passed on to the user."""
_RENEWALS_PER_TTL = 3


@router.get(
    "/screenshot",
    response_class=Response,
    responses={200: {"content": {"image/jpeg": {}}}, 409: CONFLICT, 503: UNAVAILABLE},
)
async def read_screenshot(
    chat_id: UUID, _: OwnedChatDep, session: SessionDep, services: ServicesDep
) -> Response:
    """Return a JPEG of the Computer's screen, starting the Computer if needed.

    A hibernated workspace answers 409; `wake` it first.
    """
    response = await call_worker(services, session, chat_id, "screenshot")
    return Response(
        response.content, media_type="image/jpeg", headers={"cache-control": "no-store"}
    )


@router.get("/status")
async def read_computer_status(
    chat_id: UUID, _: OwnedChatDep, session: SessionDep, services: ServicesDep
) -> ReadComputerStatusResult:
    """Return the workspace lifecycle, the Computer's health and usage, and recent failures.

    It does not wake a hibernated workspace. When the worker hosting the workspace does not
    answer, the stored lifecycle is returned with `computer: null`.
    """
    try:
        response = await call_worker(services, session, chat_id, "status")
    except WorkspaceHibernatedError, UnavailableError:
        report = lifecycle(await session.get(db.Workspace, chat_id), services.settings)
        report["failures"] = await recent_failures(services.sessions, chat_id)
        return ReadComputerStatusResult(data=ComputerStatus.model_validate(report))
    return ReadComputerStatusResult(data=ComputerStatus.model_validate_json(response.content))


@router.post("/control")
async def update_control(
    chat_id: UUID, _: OwnedChatDep, body: UpdateControlArgs, services: ServicesDep
) -> UpdateControlResult:
    """Take control of the Computer from the agent, or return it.

    Taking control during an agent call answers 409; retry after it.
    """
    key = gui_key(chat_id)
    held = await services.leases.current(key)
    if body.data.action is ControlAction.RELEASE:
        if held is not None and held.holder == USER:
            await services.leases.release(key, USER, held.token)
        return UpdateControlResult(data=Control(held=False))
    ttl = services.settings.computer.control_ttl
    if held is not None and held.holder == USER:
        await services.leases.renew(key, USER, held.token, ttl)
    elif await services.leases.acquire(key, USER, ttl) is None:
        raise ComputerBusyError("an agent call holds the Computer; retry after it")
    held = await services.leases.current(key)
    if held is None or held.holder != USER:
        raise ComputerBusyError("an agent call holds the Computer; retry after it")
    return UpdateControlResult(
        data=Control(held=True, token=held.token, expires_at=held.expires_at)
    )


@router.websocket("/vnc")
async def vnc(
    websocket: WebSocket,
    chat_id: UUID,
    services: ServicesDep,
    *,
    control: Annotated[
        bool, Query(description="Control the screen; needs control taken first")
    ] = False,
) -> None:
    """RFB stream of the Computer's screen for noVNC (subprotocol `binary`).

    Viewing is always allowed; on a hibernated workspace the stream sends `wake` and closes
    with 4409 `workspace_hibernated`, so reconnect shortly. `control=true` needs control taken
    through `POST control`; it is kept while the stream is open and returned when it closes.
    Close codes: 4401 unauthorized, 4404 no chat, 4409 conflict, 4503 worker unavailable.
    The sign-in is checked again every few seconds; the stream closes once it ends.
    """
    principal = await authenticate(websocket, services)
    if principal is None:
        await websocket.close(CLOSE_UNAUTHORIZED, "unauthorized")
        return
    requested = websocket.scope.get("subprotocols", [])
    await websocket.accept(subprotocol="binary" if "binary" in requested else None)
    code, reason = await _stream(websocket, services, principal, chat_id, control=control)
    with contextlib.suppress(RuntimeError, WebSocketDisconnect):
        await websocket.close(code, reason)


async def _stream(
    websocket: WebSocket, services: Services, principal: Principal, chat_id: UUID, *, control: bool
) -> tuple[int, str]:
    """Relay the screen and return the close code and reason for the user."""
    async with services.sessions() as session:
        chat = await session.get(db.Chat, chat_id)
        if chat is None or chat.created_by != principal.user_id or chat.deleted_at is not None:
            return CLOSE_NOT_FOUND, "not_found"
        try:
            url = await worker_url(session, chat_id)
        except WorkspaceHibernatedError:
            url = None
        except XavierError:
            return CLOSE_UNAVAILABLE, "worker_unavailable"
    if url is None:
        with contextlib.suppress(XavierError):
            await services.commands.workspace(chat_id, "wake_workspace")
        return CLOSE_CONFLICT, "workspace_hibernated"
    key, token = gui_key(chat_id), 0
    if control:
        held = await services.leases.current(key)
        if held is None or held.holder != USER:
            return CLOSE_CONFLICT, CONTROL_NOT_HELD
        token = held.token
    target = f"ws{url.removeprefix('http')}/vnc?control={int(control)}&token={token}"
    try:
        async with connect(
            target,
            additional_headers=worker_headers(services),
            proxy=None,
            compression=None,
            max_size=None,
        ) as upstream:
            keep = _keep_control(services, key, token) if control else None
            return await _relay(websocket, upstream, keep, _keep_principal(services, principal))
    except OSError, InvalidHandshake:
        return CLOSE_UNAVAILABLE, "worker_unavailable"
    finally:
        if control:
            await services.leases.release(key, USER, token)


async def _keep_control(services: Services, key: str, token: int) -> tuple[int, str]:
    """Renew the user lease until it is lost, then return the close of the stream."""
    ttl: timedelta = services.settings.computer.control_ttl
    while True:
        await asyncio.sleep(ttl.total_seconds() / _RENEWALS_PER_TTL)
        if not await services.leases.renew(key, USER, token, ttl):
            return CLOSE_CONFLICT, CONTROL_NOT_HELD


async def _keep_principal(services: Services, principal: Principal) -> tuple[int, str]:
    """Return the close of the stream once the session or API key is no longer valid."""
    while True:
        await asyncio.sleep(STREAM_CHECK_S)
        if not await still_active(services, principal):
            return CLOSE_UNAUTHORIZED, "unauthorized"


async def _relay(
    websocket: WebSocket,
    upstream: ClientConnection,
    keep: Awaitable[tuple[int, str]] | None,
    auth: Awaitable[tuple[int, str]],
) -> tuple[int, str]:
    async def to_worker() -> tuple[int, str]:
        with contextlib.suppress(WebSocketDisconnect, ConnectionClosed):
            while True:
                await upstream.send(await websocket.receive_bytes())
        return 1000, ""

    async def to_user() -> tuple[int, str]:
        with contextlib.suppress(ConnectionClosed, WebSocketDisconnect):
            async for message in upstream:
                await websocket.send_bytes(
                    message if isinstance(message, bytes) else message.encode()
                )
        code = upstream.close_code
        if code is not None and code in _APPLICATION_CODES:
            return code, upstream.close_reason or ""
        return 1000, ""

    tasks = [
        asyncio.ensure_future(to_worker()),
        asyncio.ensure_future(to_user()),
        asyncio.ensure_future(auth),
    ]
    if keep is not None:
        tasks.append(asyncio.ensure_future(keep))
    try:
        done, _pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    return next(iter(done)).result()
