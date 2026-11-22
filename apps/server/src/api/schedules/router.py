"""Chat schedules post a message to the agent on a timetable.

A schedule posts its `text` at a one-off `timing.at`, on an `interval` or on a `cron`
timetable, into a pinned chat or into a new chat per run. A one-off schedule stays listed with
its run until it is deleted. New chats take the schedule's settings, resolved with the defaults
when the schedule is stored; chats a schedule created are kept when it is deleted. Schedules are
stored in Temporal, so their calls answer 503 while Temporal is unreachable.
"""

import uuid
from typing import Annotated
from uuid import UUID

from fastapi import Query, status

from src.api.chats.service import resolve_model, stored_max_steps, title
from src.api.common import (
    INVALID_REQUEST,
    NOT_FOUND,
    UNAVAILABLE,
    SortDirection,
    paginate_items,
    router,
)
from src.api.dependencies import CurrentUserDep, Services, ServicesDep, SessionDep, chat_visible
from src.api.schedules.schemas import (
    CreateScheduleArgs,
    CreateScheduleInput,
    CreateScheduleResult,
    DeleteScheduleResult,
    ListSchedulesInput,
    ListSchedulesResult,
    PauseScheduleResult,
    ReadScheduleResult,
    ReplaceScheduleArgs,
    ReplaceScheduleResult,
    ResumeScheduleResult,
    Schedule,
    ScheduleTemplate,
    TriggerScheduleResult,
)
from src.errors import NotFoundError
from src.orchestration.models import ChatScheduleRun, ChatTemplate
from src.orchestration.schedules import ChatSchedule

router = router("/schedules", "schedules")


@router.get("", responses={503: UNAVAILABLE})
async def list_schedules(
    principal: CurrentUserDep, services: ServicesDep, params: Annotated[ListSchedulesInput, Query()]
) -> ListSchedulesResult:
    """List the user's chat schedules with their next and recent run times."""
    items = [
        schedule
        for schedule in map(_out, await services.chat_schedules.all(principal.user_id))
        if (not params.chat_id or schedule.chat_id in params.chat_id)
        and (params.paused is None or schedule.paused is params.paused)
    ]
    items.sort(
        key=lambda schedule: (getattr(schedule, params.sort), schedule.id),
        reverse=params.direction is SortDirection.DESC,
    )
    data, info = paginate_items(items, params)
    return ListSchedulesResult(data=data, info=info)


@router.post(
    "", status_code=status.HTTP_201_CREATED, responses={422: INVALID_REQUEST, 503: UNAVAILABLE}
)
async def create_schedule(
    body: CreateScheduleArgs, principal: CurrentUserDep, session: SessionDep, services: ServicesDep
) -> CreateScheduleResult:
    """Create a schedule."""
    data = body.data
    if data.chat_id is not None and not await chat_visible(
        session, data.chat_id, principal.user_id
    ):
        raise NotFoundError(f"chat {data.chat_id} not found")
    run = _run(services, data, principal.user_id)
    schedule = await services.chat_schedules.put(uuid.uuid7(), run, paused=data.paused)
    return CreateScheduleResult(data=_out(schedule))


@router.get("/{schedule_id}", responses={404: NOT_FOUND, 503: UNAVAILABLE})
async def read_schedule(
    schedule_id: UUID, principal: CurrentUserDep, services: ServicesDep
) -> ReadScheduleResult:
    """Return a schedule with its next and recent run times."""
    schedule = await services.chat_schedules.get(schedule_id, principal.user_id)
    return ReadScheduleResult(data=_out(schedule))


@router.put("/{schedule_id}", responses={404: NOT_FOUND, 422: INVALID_REQUEST, 503: UNAVAILABLE})
async def replace_schedule(
    schedule_id: UUID,
    body: ReplaceScheduleArgs,
    principal: CurrentUserDep,
    session: SessionDep,
    services: ServicesDep,
) -> ReplaceScheduleResult:
    """Replace a schedule with new settings."""
    data = body.data
    await services.chat_schedules.get(schedule_id, principal.user_id)
    if data.chat_id is not None and not await chat_visible(
        session, data.chat_id, principal.user_id
    ):
        raise NotFoundError(f"chat {data.chat_id} not found")
    run = _run(services, data, principal.user_id)
    schedule = await services.chat_schedules.put(schedule_id, run, paused=data.paused)
    return ReplaceScheduleResult(data=_out(schedule))


@router.delete("/{schedule_id}", responses={404: NOT_FOUND, 503: UNAVAILABLE})
async def delete_schedule(
    schedule_id: UUID, principal: CurrentUserDep, services: ServicesDep
) -> DeleteScheduleResult:
    """Delete a schedule."""
    schedule = await services.chat_schedules.delete(schedule_id, principal.user_id)
    return DeleteScheduleResult(data=_out(schedule))


@router.post("/{schedule_id}/pause", responses={404: NOT_FOUND, 503: UNAVAILABLE})
async def pause_schedule(
    schedule_id: UUID, principal: CurrentUserDep, services: ServicesDep
) -> PauseScheduleResult:
    """Stop posting until resumed."""
    schedule = await services.chat_schedules.pause(schedule_id, principal.user_id)
    return PauseScheduleResult(data=_out(schedule))


@router.post("/{schedule_id}/resume", responses={404: NOT_FOUND, 503: UNAVAILABLE})
async def resume_schedule(
    schedule_id: UUID, principal: CurrentUserDep, services: ServicesDep
) -> ResumeScheduleResult:
    """Post on the timing again."""
    schedule = await services.chat_schedules.resume(schedule_id, principal.user_id)
    return ResumeScheduleResult(data=_out(schedule))


@router.post("/{schedule_id}/trigger", responses={404: NOT_FOUND, 503: UNAVAILABLE})
async def trigger_schedule(
    schedule_id: UUID, principal: CurrentUserDep, services: ServicesDep
) -> TriggerScheduleResult:
    """Post the message now, outside the timing."""
    await services.chat_schedules.trigger(schedule_id, principal.user_id)
    schedule = await services.chat_schedules.get(schedule_id, principal.user_id)
    return TriggerScheduleResult(data=_out(schedule))


def _run(services: Services, data: CreateScheduleInput, owner: UUID) -> ChatScheduleRun:
    template = None
    if data.chat_id is None:
        settings = services.settings
        model, reasoning = resolve_model(settings, data.model, data.reasoning)
        template = ChatTemplate(
            title=data.title or title(data.text),
            model=model,
            reasoning=reasoning,
            mode=data.mode or settings.chat.default_mode,
            tool_groups=sorted({group.value for group in data.tool_groups}),
            execute_network=data.execute_network,
            budget_usd=(
                settings.chat.default_budget_usd if data.budget_usd is None else data.budget_usd
            ),
            max_steps=stored_max_steps(
                data.model_fields_set, data.max_steps, settings.turn.max_steps
            ),
        )
    return ChatScheduleRun(
        created_by=owner,
        name=data.name,
        timing=data.timing,
        text=data.text,
        chat_id=data.chat_id,
        template=template,
    )


def _out(schedule: ChatSchedule) -> Schedule:
    run = schedule.run
    return Schedule(
        id=schedule.id,
        name=run.name,
        timing=run.timing,
        text=run.text,
        chat_id=run.chat_id,
        template=(
            ScheduleTemplate.model_validate(run.template) if run.template is not None else None
        ),
        paused=schedule.paused,
        next_runs=schedule.next_runs,
        recent_runs=schedule.recent_runs,
        created_at=schedule.created_at,
    )
