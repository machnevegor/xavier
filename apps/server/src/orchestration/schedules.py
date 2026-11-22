"""Temporal Schedules: maintenance, Observe polling and messages into chats.

Workflows are named, not imported: activities use these schedules and workflows use activities.
"""

import contextlib
from collections.abc import Awaitable
from datetime import datetime, timedelta
from uuid import UUID

from pydantic import BaseModel
from temporalio.client import (
    Client,
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleAlreadyRunningError,
    ScheduleCalendarSpec,
    ScheduleDescription,
    ScheduleHandle,
    ScheduleIntervalSpec,
    ScheduleOverlapPolicy,
    SchedulePolicy,
    ScheduleRange,
    ScheduleSpec,
    ScheduleState,
    ScheduleUpdate,
)
from temporalio.service import RPCError, RPCStatusCode

from src.errors import NotFoundError, UnavailableError
from src.observe.spec import Schedule as Timing
from src.orchestration.models import ChatScheduleRun, ChatTiming

MAINTENANCE_SCHEDULE = "xavier-maintenance"
MAINTENANCE_INTERVAL = timedelta(minutes=10)
_SOURCE_PREFIX = "xv-observe-"
_CHAT_PREFIX = "xv-chat-"
OWNER_MEMO = "created_by"
"""Memo key of the owner of a chat schedule."""


async def ensure_maintenance_schedule(client: Client, task_queue: str) -> None:
    """Create the schedule of `MaintenanceWorkflow` unless it exists."""
    schedule = Schedule(
        action=ScheduleActionStartWorkflow(
            "MaintenanceWorkflow", id=MAINTENANCE_SCHEDULE, task_queue=task_queue
        ),
        spec=ScheduleSpec(intervals=[ScheduleIntervalSpec(every=MAINTENANCE_INTERVAL)]),
        policy=SchedulePolicy(overlap=ScheduleOverlapPolicy.SKIP),
    )
    with contextlib.suppress(ScheduleAlreadyRunningError):
        await client.create_schedule(MAINTENANCE_SCHEDULE, schedule)


def timing_spec(timing: Timing) -> ScheduleSpec:
    """Return the Temporal spec of an interval or a cron expression in a time zone."""
    if timing.interval is not None:
        return ScheduleSpec(intervals=[ScheduleIntervalSpec(every=timing.interval)])
    return ScheduleSpec(cron_expressions=[timing.cron or ""], time_zone_name=timing.time_zone)


class SourceSchedules:
    """The polling schedule of each published Observe source, id ``xv-observe-<source id>``."""

    def __init__(self, client: Client, task_queue: str) -> None:
        self._client = client
        self._task_queue = task_queue

    async def put(self, source_id: UUID, timing: Timing, *, paused: bool) -> None:
        """Create or replace the schedule of a source."""
        schedule = Schedule(
            action=ScheduleActionStartWorkflow(
                "ObservePollWorkflow",
                source_id,
                id=f"xv-observe-poll-{source_id}",
                task_queue=self._task_queue,
            ),
            spec=timing_spec(timing),
            policy=SchedulePolicy(overlap=ScheduleOverlapPolicy.SKIP),
            state=ScheduleState(paused=paused),
        )
        schedule_id = f"{_SOURCE_PREFIX}{source_id}"
        try:
            await self._client.create_schedule(schedule_id, schedule)
        except ScheduleAlreadyRunningError:
            await _call(
                self._client.get_schedule_handle(schedule_id).update(
                    lambda _: ScheduleUpdate(schedule=schedule)
                )
            )
        except RPCError as error:
            raise UnavailableError from error

    async def pause(self, source_id: UUID, note: str) -> None:
        """Stop starting polls of a source."""
        await _call(self._handle(source_id).pause(note=note))

    async def resume(self, source_id: UUID) -> None:
        """Start polls of a source again."""
        await _call(self._handle(source_id).unpause())

    async def trigger(self, source_id: UUID) -> None:
        """Poll a source now unless a poll is running."""
        await _call(self._handle(source_id).trigger(overlap=ScheduleOverlapPolicy.SKIP))

    async def delete(self, source_id: UUID) -> None:
        """Delete the schedule of a source if it exists."""
        with contextlib.suppress(NotFoundError):
            await _call(self._handle(source_id).delete())

    async def recent_runs(self, source_id: UUID) -> list[datetime]:
        """Return the scheduled times of the latest polls the schedule started."""
        description = await _call(self._handle(source_id).describe())
        return [action.scheduled_at for action in description.info.recent_actions]

    def _handle(self, source_id: UUID) -> ScheduleHandle:
        return self._client.get_schedule_handle(f"{_SOURCE_PREFIX}{source_id}")


def _chat_spec(timing: ChatTiming) -> ScheduleSpec:
    """Return the spec of a timetable, or a calendar matching only the second of ``at``."""
    if timing.at is None:
        return timing_spec(timing)
    at = timing.at
    return ScheduleSpec(
        calendars=[
            ScheduleCalendarSpec(
                second=[ScheduleRange(at.second)],
                minute=[ScheduleRange(at.minute)],
                hour=[ScheduleRange(at.hour)],
                day_of_month=[ScheduleRange(at.day)],
                month=[ScheduleRange(at.month)],
                year=[ScheduleRange(at.year)],
            )
        ],
        time_zone_name="UTC",
    )


class ChatSchedule(BaseModel):
    """A chat schedule as stored in Temporal."""

    id: UUID
    run: ChatScheduleRun
    paused: bool
    next_runs: list[datetime]
    recent_runs: list[datetime]
    created_at: datetime


class ChatSchedules:
    """Schedules that post a message into a new or a pinned chat, id ``xv-chat-<uuid>``.

    The owner is kept in the schedule memo, which lists and reads are filtered by. A one-off
    schedule (``timing.at``) has one remaining action; it stays listed after its run.
    """

    def __init__(self, client: Client, task_queue: str) -> None:
        self._client = client
        self._task_queue = task_queue

    async def put(self, schedule_id: UUID, run: ChatScheduleRun, *, paused: bool) -> ChatSchedule:
        """Create or replace a schedule."""
        schedule = Schedule(
            action=ScheduleActionStartWorkflow(
                "ChatScheduleWorkflow",
                run,
                id=f"xv-chat-run-{schedule_id}",
                task_queue=self._task_queue,
            ),
            spec=_chat_spec(run.timing),
            policy=SchedulePolicy(overlap=ScheduleOverlapPolicy.ALLOW_ALL),
            state=ScheduleState(
                paused=paused,
                limited_actions=run.timing.at is not None,
                remaining_actions=1 if run.timing.at is not None else 0,
            ),
        )
        handle = self._client.get_schedule_handle(f"{_CHAT_PREFIX}{schedule_id}")
        try:
            await self._client.create_schedule(
                handle.id, schedule, memo={OWNER_MEMO: str(run.created_by)}
            )
        except ScheduleAlreadyRunningError:
            await _call(handle.update(lambda _: ScheduleUpdate(schedule=schedule)))
        except RPCError as error:
            raise UnavailableError from error
        return await self.get(schedule_id, run.created_by)

    async def all(self, owner: UUID) -> list[ChatSchedule]:
        """Return the chat schedules of ``owner``."""
        found: list[ChatSchedule] = []
        try:
            async for entry in await self._client.list_schedules():
                if entry.id.startswith(_CHAT_PREFIX) and await entry.memo_value(
                    OWNER_MEMO, None
                ) == str(owner):
                    with contextlib.suppress(NotFoundError):
                        schedule_id = UUID(entry.id.removeprefix(_CHAT_PREFIX))
                        found.append(await self.get(schedule_id, owner))
        except RPCError as error:
            raise UnavailableError from error
        return sorted(found, key=lambda schedule: schedule.id)

    async def get(self, schedule_id: UUID, owner: UUID) -> ChatSchedule:
        """Return a schedule of ``owner``.

        Raises
        ------
        NotFoundError
            If it does not exist or belongs to another user.
        """
        description = await _call(self._handle(schedule_id).describe())
        if await description.memo_value(OWNER_MEMO, None) != str(owner):
            raise NotFoundError(f"schedule {schedule_id} not found")
        return await self._view(schedule_id, description)

    async def pause(self, schedule_id: UUID, owner: UUID) -> ChatSchedule:
        """Stop starting runs."""
        await self.get(schedule_id, owner)
        await _call(self._handle(schedule_id).pause(note="paused by the owner"))
        return await self.get(schedule_id, owner)

    async def resume(self, schedule_id: UUID, owner: UUID) -> ChatSchedule:
        """Start runs again."""
        await self.get(schedule_id, owner)
        await _call(self._handle(schedule_id).unpause(note="resumed by the owner"))
        return await self.get(schedule_id, owner)

    async def trigger(self, schedule_id: UUID, owner: UUID) -> None:
        """Start a run now."""
        await self.get(schedule_id, owner)
        await _call(self._handle(schedule_id).trigger())

    async def delete(self, schedule_id: UUID, owner: UUID) -> ChatSchedule:
        """Delete a schedule; return it as it was."""
        schedule = await self.get(schedule_id, owner)
        await _call(self._handle(schedule_id).delete())
        return schedule

    def _handle(self, schedule_id: UUID) -> ScheduleHandle:
        return self._client.get_schedule_handle(f"{_CHAT_PREFIX}{schedule_id}")

    async def _view(self, schedule_id: UUID, description: ScheduleDescription) -> ChatSchedule:
        action = description.schedule.action
        if not isinstance(action, ScheduleActionStartWorkflow):
            raise NotFoundError(f"schedule {schedule_id} not found")
        [run] = await self._client.data_converter.decode(action.args, [ChatScheduleRun])
        info = description.info
        return ChatSchedule(
            id=schedule_id,
            run=run,
            paused=description.schedule.state.paused,
            next_runs=list(info.next_action_times),
            recent_runs=[action.scheduled_at for action in info.recent_actions],
            created_at=info.created_at,
        )


async def _call[T](call: Awaitable[T]) -> T:
    """Await a schedule call; map a missing schedule to 404 and other failures to 503."""
    try:
        return await call
    except RPCError as error:
        if error.status is RPCStatusCode.NOT_FOUND:
            raise NotFoundError("schedule not found") from error
        raise UnavailableError from error
