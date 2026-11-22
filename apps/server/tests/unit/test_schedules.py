import uuid
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast
from uuid import UUID

import pytest
from pydantic import ValidationError
from temporalio.client import Client, ScheduleActionStartWorkflow, ScheduleRange

from src.errors import NotFoundError
from src.orchestration.models import ChatScheduleRun, ChatTiming
from src.orchestration.schedules import OWNER_MEMO, ChatSchedules, _chat_spec


def test_one_off_timing_is_stored_in_utc_to_the_second() -> None:
    timing = ChatTiming.model_validate({"at": "2027-01-02T03:04:05.678+03:00"})

    assert timing.at == datetime(2027, 1, 2, 0, 4, 5, tzinfo=UTC)


@pytest.mark.parametrize(
    "timing",
    [
        {},
        {"at": "2027-01-02T03:04:05"},
        {"at": "2027-01-02T03:04:05Z", "cron": "0 9 * * *"},
        {"at": "2027-01-02T03:04:05Z", "interval": "PT1H"},
    ],
)
def test_timing_needs_exactly_one_aware_kind(timing: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        ChatTiming.model_validate(timing)


def test_one_off_spec_matches_only_its_second() -> None:
    spec = _chat_spec(ChatTiming(at=datetime(2027, 1, 2, 0, 4, 5, tzinfo=UTC)))

    [calendar] = spec.calendars
    assert spec.time_zone_name == "UTC"
    assert (
        calendar.year,
        calendar.month,
        calendar.day_of_month,
        calendar.hour,
        calendar.minute,
        calendar.second,
    ) == (
        [ScheduleRange(2027)],
        [ScheduleRange(1)],
        [ScheduleRange(2)],
        [ScheduleRange(0)],
        [ScheduleRange(4)],
        [ScheduleRange(5)],
    )


def test_timetable_spec_is_unchanged() -> None:
    spec = _chat_spec(ChatTiming(cron="0 9 * * 1-5", time_zone="Europe/Moscow"))

    assert spec.cron_expressions == ["0 9 * * 1-5"]
    assert spec.time_zone_name == "Europe/Moscow"
    assert spec.calendars == []


class FakeSchedule:
    """A stored chat schedule as listed and described by Temporal, with its memo."""

    def __init__(self, owner: UUID) -> None:
        self.run = ChatScheduleRun(
            created_by=owner,
            name="daily",
            timing=ChatTiming(cron="0 9 * * *"),
            text="report",
            chat_id=uuid.uuid4(),
            template=None,
        )
        self._memo = {OWNER_MEMO: str(owner)}
        self.schedule = SimpleNamespace(
            action=ScheduleActionStartWorkflow(
                "ChatScheduleWorkflow", self.run, id="run", task_queue="xavier"
            ),
            state=SimpleNamespace(paused=False),
        )
        self.info = SimpleNamespace(
            next_action_times=[], recent_actions=[], created_at=datetime.now(UTC)
        )

    async def memo_value(self, key: str, default: object) -> object:
        return self._memo.get(key, default)


class FakeConverter:
    async def decode(self, args: Sequence[object], types: object) -> list[object]:
        del types
        return list(args)


class FakeTemporal:
    data_converter = FakeConverter()

    def __init__(self, schedules: dict[UUID, FakeSchedule]) -> None:
        self.schedules = {f"xv-chat-{key}": schedule for key, schedule in schedules.items()}
        self.described: list[str] = []

    async def list_schedules(self) -> AsyncIterator[SimpleNamespace]:
        return self._entries()

    async def _entries(self) -> AsyncIterator[SimpleNamespace]:
        for schedule_id, schedule in self.schedules.items():
            yield SimpleNamespace(id=schedule_id, memo_value=schedule.memo_value)

    def get_schedule_handle(self, schedule_id: str) -> SimpleNamespace:
        async def describe() -> FakeSchedule:
            self.described.append(schedule_id)
            return self.schedules[schedule_id]

        return SimpleNamespace(id=schedule_id, describe=describe)


async def test_chat_schedules_are_listed_and_read_only_by_their_owner() -> None:
    owner, other = uuid.uuid4(), uuid.uuid4()
    mine, theirs = uuid.uuid4(), uuid.uuid4()
    temporal = FakeTemporal({mine: FakeSchedule(owner), theirs: FakeSchedule(other)})
    schedules = ChatSchedules(cast(Client, temporal), "xavier")

    listed = await schedules.all(owner)

    assert [schedule.id for schedule in listed] == [mine]
    assert temporal.described == [f"xv-chat-{mine}"]
    with pytest.raises(NotFoundError):
        await schedules.get(theirs, owner)
