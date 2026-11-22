"""The chat workspace: its state, wake, hibernate, delete and files.

An *active* workspace has running volumes on a worker; a *hibernated* one is kept as a
checkpoint and is restored by the next tool call or `wake`. Commands are delivered to the chat
workflow and return the workspace state when they are accepted; it changes once the workflow
runs them, during a turn after it. Files are read from an active workspace only; a hibernated
one answers 409.
"""

from typing import Annotated, Literal
from urllib.parse import quote
from uuid import UUID

from fastapi import Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.background import BackgroundTask

from src.api.common import CONFLICT, PAYLOAD_TOO_LARGE, UNAVAILABLE, router
from src.api.dependencies import OwnedChatDep, Services, ServicesDep, SessionDep
from src.api.workspace.schemas import (
    DeleteWorkspaceResult,
    HibernateWorkspaceResult,
    ListWorkspaceFilesInput,
    ListWorkspaceFilesResult,
    ReadWorkspaceFileInput,
    ReadWorkspaceResult,
    WakeWorkspaceResult,
    Workspace,
    WorkspaceDirectory,
)
from src.api.workspace.service import call_worker
from src.db import models as db

router = router("/chats/{chat_id}/workspace", "workspace")


@router.get("")
async def read_workspace(
    chat_id: UUID, _: OwnedChatDep, session: SessionDep
) -> ReadWorkspaceResult:
    """Return the state of the chat workspace."""
    return ReadWorkspaceResult(data=await _state(session, chat_id))


@router.post("/wake", responses={503: UNAVAILABLE})
async def wake_workspace(
    chat_id: UUID, _: OwnedChatDep, session: SessionDep, services: ServicesDep
) -> WakeWorkspaceResult:
    """Activate the workspace, restoring its last checkpoint."""
    return WakeWorkspaceResult(data=await _command(services, session, chat_id, "wake_workspace"))


@router.post("/hibernate", responses={503: UNAVAILABLE})
async def hibernate_workspace(
    chat_id: UUID, _: OwnedChatDep, session: SessionDep, services: ServicesDep
) -> HibernateWorkspaceResult:
    """Checkpoint and release the workspace. Running code runs keep it active."""
    return HibernateWorkspaceResult(
        data=await _command(services, session, chat_id, "hibernate_workspace")
    )


@router.delete("", responses={503: UNAVAILABLE})
async def delete_workspace(
    chat_id: UUID, _: OwnedChatDep, session: SessionDep, services: ServicesDep
) -> DeleteWorkspaceResult:
    """Delete the workspace files and every checkpoint. This cannot be undone."""
    return DeleteWorkspaceResult(
        data=await _command(services, session, chat_id, "delete_workspace")
    )


@router.get("/files", responses={409: CONFLICT, 413: PAYLOAD_TOO_LARGE, 503: UNAVAILABLE})
async def list_workspace_files(
    chat_id: UUID,
    _: OwnedChatDep,
    session: SessionDep,
    services: ServicesDep,
    params: Annotated[ListWorkspaceFilesInput, Query()],
) -> ListWorkspaceFilesResult:
    """List a directory of the active workspace."""
    response = await call_worker(services, session, chat_id, "files", params={"path": params.path})
    return ListWorkspaceFilesResult(data=WorkspaceDirectory.model_validate_json(response.content))


@router.get(
    "/files/content",
    response_class=StreamingResponse,
    responses={
        200: {"content": {"application/octet-stream": {}}, "description": "The file"},
        409: CONFLICT,
        413: PAYLOAD_TOO_LARGE,
        503: UNAVAILABLE,
    },
)
async def read_workspace_file_content(
    chat_id: UUID,
    _: OwnedChatDep,
    session: SessionDep,
    services: ServicesDep,
    params: Annotated[ReadWorkspaceFileInput, Query()],
) -> StreamingResponse:
    """Download a file of the active workspace (64 MiB at most)."""
    response = await call_worker(
        services, session, chat_id, "content", params={"path": params.path}, stream=True
    )
    name = params.path.rstrip("/").rsplit("/", 1)[-1]
    return StreamingResponse(
        response.aiter_bytes(),
        media_type="application/octet-stream",
        headers={
            "content-length": response.headers["content-length"],
            "content-disposition": f"attachment; filename*=UTF-8''{quote(name, safe='')}",
        },
        background=BackgroundTask(response.aclose),
    )


async def _state(session: AsyncSession, chat_id: UUID) -> Workspace:
    row = await session.get(db.Workspace, chat_id)
    if row is None:
        return Workspace(status=None)
    return Workspace.model_validate(row)


async def _command(
    services: Services,
    session: AsyncSession,
    chat_id: UUID,
    signal: Literal["wake_workspace", "hibernate_workspace", "delete_workspace"],
) -> Workspace:
    await services.commands.workspace(chat_id, signal)
    return await _state(session, chat_id)
