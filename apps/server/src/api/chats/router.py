"""Chats: creation, settings, commands and deletion.

A chat is a conversation with the agent and belongs to the user who created it. Its settings
apply from the next turn; a running turn keeps the previous ones. Commands (`pause`, `resume`,
`stop`) return the chat with its status after the command.

Deleting a chat stops its current turn, cancels its wakeups and pending decisions and
hibernates its workspace. A deleted chat disappears from lists and links at once and is purged
with its files after the configured retention; it is not found from then on.

A 503 means the change is stored but the agent was not notified; retrying, sending a message
or another command resumes it. A 409 on a command means the chat was deleted meanwhile.
"""

import uuid
from typing import Annotated
from uuid import UUID

from fastapi import Query, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.agent import capabilities, proactive
from src.api.chats.schemas import (
    Chat,
    ChatsSort,
    CreateChatArgs,
    CreateChatResult,
    DeleteChatResult,
    ListChatsInput,
    ListChatsResult,
    PauseChatResult,
    ReadChatResult,
    ResumeChatResult,
    StopChatResult,
    UpdateChatArgs,
    UpdateChatInput,
    UpdateChatResult,
)
from src.api.chats.service import resolve_model, stored_max_steps, title
from src.api.common import (
    CONFLICT,
    INVALID_REQUEST,
    NOT_FOUND,
    UNAVAILABLE,
    paginate,
    router,
)
from src.api.dependencies import CurrentUserDep, OwnedChatDep, Services, ServicesDep, SessionDep
from src.api.messages.service import add_message
from src.config import Settings
from src.db import models as db
from src.errors import BudgetExhaustedError
from src.orchestration.models import CommandResult

router = router("/chats", "chats")

_SORT = {
    ChatsSort.ID: db.Chat.id,
    ChatsSort.CREATED_AT: db.Chat.created_at,
    ChatsSort.UPDATED_AT: db.Chat.updated_at,
}


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    responses={404: NOT_FOUND, 422: INVALID_REQUEST, 503: UNAVAILABLE},
)
async def create_chat(
    args: CreateChatArgs, principal: CurrentUserDep, session: SessionDep, services: ServicesDep
) -> CreateChatResult:
    """Create a chat with its first message and start the agent.

    Images and files must be uploaded first with `POST /artifacts`; 404 when one is not found.
    """
    data = args.data
    settings = services.settings
    model_id, reasoning = resolve_model(settings, data.model, data.reasoning)
    chat = db.Chat(
        id=uuid.uuid7(),
        created_by=principal.user_id,
        title=data.title or title(data.text),
        title_source=db.TitleSource.USER if data.title else db.TitleSource.FALLBACK,
        status=db.ChatStatus.WORKING,
        model=model_id,
        reasoning=reasoning,
        mode=data.mode or settings.chat.default_mode,
        tool_groups=sorted({group.value for group in data.tool_groups}),
        execute_network=data.execute_network,
        proactive=data.proactive,
        budget_usd=settings.chat.default_budget_usd if data.budget_usd is None else data.budget_usd,
        max_steps=stored_max_steps(data.model_fields_set, data.max_steps, settings.turn.max_steps),
        updated_by=principal.user_id,
    )
    session.add(chat)
    await session.flush()
    await add_message(session, chat.id, principal.user_id, data)
    await session.refresh(chat)
    await session.commit()
    await services.commands.input_available(chat.id)
    return CreateChatResult(data=Chat.model_validate(chat))


@router.get("")
async def list_chats(
    params: Annotated[ListChatsInput, Query()], principal: CurrentUserDep, session: SessionDep
) -> ListChatsResult:
    """List the user's chats, most recently updated first by default."""
    query = select(db.Chat).where(
        db.Chat.created_by == principal.user_id, db.Chat.deleted_at.is_(None)
    )
    if params.status:
        query = query.where(db.Chat.status.in_(params.status))
    rows, info = await paginate(
        session, query, params, sort=_SORT[params.sort], direction=params.direction, key=db.Chat.id
    )
    active = await _computer_active(session, [row.id for row in rows])
    return ListChatsResult(data=[_chat(row, active) for row in rows], info=info)


@router.get("/{chat_id}")
async def read_chat(chat_id: UUID, chat: OwnedChatDep, session: SessionDep) -> ReadChatResult:
    """Return the chat with its settings, status and spending."""
    return ReadChatResult(data=_chat(chat, await _computer_active(session, [chat_id])))


@router.patch("/{chat_id}", responses={422: INVALID_REQUEST, 503: UNAVAILABLE})
async def update_chat(
    chat_id: UUID,
    args: UpdateChatArgs,
    chat: OwnedChatDep,
    session: SessionDep,
    services: ServicesDep,
) -> UpdateChatResult:
    """Change the chat settings.

    A title set here is pinned: generated titles no longer replace it. Raising `budget_usd`
    above the spending lets a chat paused by its budget be resumed. Turning `proactive` off
    cancels pending wakeups. Changing a capability the agent asked for and the operator
    declined lets the agent ask for it again.
    """
    await session.refresh(chat, with_for_update=True)
    before = capabilities.enabled(chat)
    mode = chat.mode
    _apply_settings(services.settings, chat, args.data, chat.created_by)
    if args.data.proactive is False:
        await proactive.cancel(session, chat_id)
    notify_agent = capabilities.changed(chat, before) or chat.mode is not mode
    await session.flush()
    await session.refresh(chat)
    out = _chat(chat, await _computer_active(session, [chat_id]))
    if notify_agent:
        await session.commit()
        await services.commands.settings_changed(chat_id, proactive=out.proactive)
    return UpdateChatResult(data=out)


@router.delete("/{chat_id}", responses={409: CONFLICT, 503: UNAVAILABLE})
async def delete_chat(
    chat_id: UUID,
    _: OwnedChatDep,
    principal: CurrentUserDep,
    session: SessionDep,
    services: ServicesDep,
) -> DeleteChatResult:
    """Delete the chat; returns it with `deleted_at` set."""
    await _attribute(session, chat_id, principal.user_id)
    await session.commit()
    await services.commands.delete(chat_id, principal.user_id)
    return DeleteChatResult(data=await _after_command(services, chat_id, None))


@router.post("/{chat_id}/pause", responses={409: CONFLICT, 503: UNAVAILABLE})
async def pause_chat(
    chat_id: UUID,
    _: OwnedChatDep,
    principal: CurrentUserDep,
    session: SessionDep,
    services: ServicesDep,
) -> PauseChatResult:
    """Interrupt the current turn and start no new turns until resumed.

    Pending decisions are kept and still accepted; approved actions run after resume.
    """
    await _attribute(session, chat_id, principal.user_id)
    await session.commit()
    result = await services.commands.pause(chat_id)
    return PauseChatResult(data=await _after_command(services, chat_id, result))


@router.post("/{chat_id}/resume", responses={409: CONFLICT, 503: UNAVAILABLE})
async def resume_chat(
    chat_id: UUID,
    chat: OwnedChatDep,
    principal: CurrentUserDep,
    session: SessionDep,
    services: ServicesDep,
) -> ResumeChatResult:
    """Lift a pause; queued messages or decisions start a turn.

    A chat paused by its budget can be resumed only after the budget is raised; 409 until then.
    """
    if chat.pause_reason is db.PauseReason.BUDGET and chat.spent_usd >= chat.budget_usd:
        raise BudgetExhaustedError("raise the budget above the spending to resume the chat")
    await _attribute(session, chat_id, principal.user_id)
    await session.commit()
    result = await services.commands.resume(chat_id)
    return ResumeChatResult(data=await _after_command(services, chat_id, result))


@router.post("/{chat_id}/stop", responses={409: CONFLICT, 503: UNAVAILABLE})
async def stop_chat(
    chat_id: UUID,
    _: OwnedChatDep,
    principal: CurrentUserDep,
    session: SessionDep,
    services: ServicesDep,
) -> StopChatResult:
    """Interrupt the current turn and cancel unfinished actions and pending decisions."""
    await _attribute(session, chat_id, principal.user_id)
    await session.commit()
    result = await services.commands.stop(chat_id)
    return StopChatResult(data=await _after_command(services, chat_id, result))


async def _after_command(services: Services, chat_id: UUID, result: CommandResult | None) -> Chat:
    """Return the chat after a command; the command's status wins over a stale projection.

    The request transaction is already committed, so the workflow can write the chat. This reads
    that write in a new session.
    """
    async with services.sessions() as session:
        chat = await session.get_one(db.Chat, chat_id)
        out = _chat(chat, await _computer_active(session, [chat_id]))
    if result is None:
        return out
    return out.model_copy(update={"status": result.status, "pause_reason": result.pause_reason})


async def _computer_active(session: AsyncSession, chat_ids: list[UUID]) -> set[UUID]:
    rows = await session.scalars(
        select(db.Workspace.chat_id).where(
            db.Workspace.chat_id.in_(chat_ids), db.Workspace.computer_id.is_not(None)
        )
    )
    return set(rows.all())


def _chat(chat: db.Chat, computer_active: set[UUID]) -> Chat:
    return Chat.model_validate(chat).model_copy(
        update={"computer_active": chat.id in computer_active}
    )


def _apply_settings(settings: Settings, chat: db.Chat, data: UpdateChatInput, actor: UUID) -> None:
    if data.model is not None or data.reasoning is not None:
        model_id = data.model or chat.model
        same = data.reasoning is None and model_id == chat.model
        reasoning = chat.reasoning if same else data.reasoning
        chat.model, chat.reasoning = resolve_model(settings, model_id, reasoning)
    if data.title is not None:
        chat.title = data.title
        chat.title_source = db.TitleSource.USER
    if data.mode is not None:
        chat.mode = data.mode
    if data.tool_groups is not None:
        chat.tool_groups = sorted({group.value for group in data.tool_groups})
    if data.execute_network is not None:
        chat.execute_network = data.execute_network
    if data.proactive is not None:
        chat.proactive = data.proactive
    if data.budget_usd is not None:
        chat.budget_usd = data.budget_usd
    if "max_steps" in data.model_fields_set:
        chat.max_steps = data.max_steps
    chat.updated_by = actor


async def _attribute(session: AsyncSession, chat_id: UUID, actor: UUID) -> None:
    """Record the user who asked for a command, without touching the projected status."""
    await session.execute(update(db.Chat).where(db.Chat.id == chat_id).values(updated_by=actor))
