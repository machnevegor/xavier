"""Requests to the worker hosting a chat workspace."""

import json
from uuid import UUID

import httpx2
from fastapi import status
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import Services
from src.db import models as db
from src.errors import (
    InvalidRequestError,
    NotFoundError,
    PayloadTooLargeError,
    UnavailableError,
    WorkspaceHibernatedError,
    XavierError,
)

_WORKER_ERRORS: dict[int, type[XavierError]] = {
    400: InvalidRequestError,
    404: NotFoundError,
    409: WorkspaceHibernatedError,
    413: PayloadTooLargeError,
}


async def worker_url(session: AsyncSession, chat_id: UUID) -> str:
    """Return the internal URL of the chat on the worker hosting its active workspace.

    The caller checks that the chat belongs to the user.

    Raises
    ------
    WorkspaceHibernatedError
        If the workspace is not active.
    UnavailableError
        If the worker hosting it is not registered.
    """
    workspace = await session.get(db.Workspace, chat_id)
    if workspace is None or workspace.status is not db.WorkspaceStatus.ACTIVE:
        raise WorkspaceHibernatedError(f"the workspace of chat {chat_id} is not active")
    worker = await session.get(db.Worker, workspace.host)
    if worker is None:
        raise UnavailableError("the worker hosting the workspace is not registered")
    return f"{worker.internal_url}/internal/workspaces/{chat_id}"


def worker_headers(services: Services) -> dict[str, str]:
    """Return the authorization of worker internal servers."""
    token = services.settings.worker.internal_token.get_secret_value()
    return {"authorization": f"Bearer {token}"}


async def call_worker(  # noqa: PLR0913 - one request to a worker
    services: Services,
    session: AsyncSession,
    chat_id: UUID,
    resource: str,
    *,
    params: dict[str, str] | None = None,
    stream: bool = False,
) -> httpx2.Response:
    """Call the internal server of the worker hosting the active workspace.

    Raises
    ------
    XavierError
        The error the worker answered with, mapped to its status; `UnavailableError` when the
        worker does not answer.
    """
    request = services.workers.build_request(
        "GET",
        f"{await worker_url(session, chat_id)}/{resource}",
        params=params,
        headers=worker_headers(services),
    )
    try:
        response = await services.workers.send(request, stream=stream)
    except httpx2.HTTPError as error:
        raise UnavailableError("the worker hosting the workspace did not answer") from error
    if response.status_code != status.HTTP_200_OK:
        detail = (await response.aread()).decode(errors="replace")
        await response.aclose()
        error_type = _WORKER_ERRORS.get(response.status_code, UnavailableError)
        raise error_type(_detail(detail))
    return response


def _detail(body: str) -> str | None:
    try:
        payload = json.loads(body)
    except ValueError:
        return None
    detail = payload.get("detail") if isinstance(payload, dict) else None
    return detail if isinstance(detail, str) else None
