"""Executions are code runs in isolated containers, started by chats or through the API.

A run started here gets an empty `/workspace` with its input artifacts, is limited in time,
memory and output, and has no internet access unless `network` is set; internal addresses stay
unreachable. Files listed in `export` become artifacts listed in `output_artifact_ids`. Runs of
a chat are listed with the chat's `chat_id`; those of a deleted chat no longer exist.
"""

from typing import Annotated
from uuid import UUID

from fastapi import Depends, Query, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from temporalio.service import RPCError

from src.api.common import paginate, router
from src.api.dependencies import CurrentUserDep, ServicesDep, SessionDep
from src.api.executions.schemas import (
    CancelExecutionResult,
    CreateExecutionArgs,
    CreateExecutionResult,
    Execution,
    ExecutionsSort,
    ListExecutionsInput,
    ListExecutionsResult,
    ReadExecutionResult,
)
from src.db import models as db
from src.errors import NotFoundError, UnavailableError
from src.execute.service import ExecutionRequest

router = router("/executions", "executions")

SORT = {
    ExecutionsSort.ID: db.Execution.id,
    ExecutionsSort.CREATED_AT: db.Execution.created_at,
}


async def get_owned_execution(
    execution_id: UUID, principal: CurrentUserDep, session: SessionDep
) -> db.Execution:
    """Return the user's run; one of a deleted chat does not exist."""
    execution = await session.get(db.Execution, execution_id)
    if (
        execution is None
        or execution.created_by != principal.user_id
        or not await _chat_visible(session, execution.chat_id)
    ):
        raise NotFoundError(f"execution {execution_id} not found")
    return execution


OwnedExecutionDep = Annotated[db.Execution, Depends(get_owned_execution)]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_execution(
    body: CreateExecutionArgs, principal: CurrentUserDep, session: SessionDep, services: ServicesDep
) -> CreateExecutionResult:
    """Start a code run and return its record at once; read it again for the result."""
    data = body.data
    missing = set(data.input_artifact_ids) - set(
        await session.scalars(
            select(db.Artifact.id)
            .outerjoin(db.Chat, db.Chat.id == db.Artifact.chat_id)
            .where(
                db.Artifact.id.in_(data.input_artifact_ids),
                db.Artifact.created_by == principal.user_id,
                or_(db.Artifact.chat_id.is_(None), db.Chat.deleted_at.is_(None)),
            )
        )
    )
    if missing:
        raise NotFoundError(f"artifact {min(missing)} not found")
    try:
        execution_id = await services.executions.start(
            ExecutionRequest(
                language=data.language,
                code=data.code,
                command=data.command,
                network=data.network,
                export=data.export,
                input_artifact_ids=data.input_artifact_ids,
            ),
            task_queue=services.settings.temporal.task_queue,
            created_by=principal.user_id,
        )
    except RPCError as error:
        raise UnavailableError from error
    execution = await services.executions.get(execution_id)
    return CreateExecutionResult(data=Execution.model_validate(execution))


@router.get("")
async def list_executions(
    principal: CurrentUserDep, session: SessionDep, params: Annotated[ListExecutionsInput, Query()]
) -> ListExecutionsResult:
    """List the user's runs, from chats and from the API."""
    query = (
        select(db.Execution)
        .outerjoin(db.Chat, db.Chat.id == db.Execution.chat_id)
        .where(
            db.Execution.created_by == principal.user_id,
            or_(db.Execution.chat_id.is_(None), db.Chat.deleted_at.is_(None)),
        )
    )
    if params.chat_id:
        query = query.where(db.Execution.chat_id.in_(params.chat_id))
    if params.status:
        query = query.where(db.Execution.status.in_(params.status))
    rows, info = await paginate(
        session,
        query,
        params,
        sort=SORT[params.sort],
        direction=params.direction,
        key=db.Execution.id,
    )
    return ListExecutionsResult(data=[Execution.model_validate(row) for row in rows], info=info)


@router.get("/{execution_id}")
async def read_execution(execution: OwnedExecutionDep) -> ReadExecutionResult:
    """Return the run with its status and, once finished, its output."""
    return ReadExecutionResult(data=Execution.model_validate(execution))


@router.post("/{execution_id}/cancel")
async def cancel_execution(
    execution: OwnedExecutionDep, services: ServicesDep
) -> CancelExecutionResult:
    """Request cancellation; the run ends as `cancelled` shortly after. Repeating is safe."""
    try:
        cancelled = await services.executions.cancel(execution.id)
    except RPCError as error:
        raise UnavailableError from error
    return CancelExecutionResult(data=Execution.model_validate(cancelled))


async def _chat_visible(session: AsyncSession, chat_id: UUID | None) -> bool:
    """Return whether ``chat_id`` is none or a chat that is not deleted."""
    if chat_id is None:
        return True
    chat = await session.get(db.Chat, chat_id)
    return chat is not None and chat.deleted_at is None
