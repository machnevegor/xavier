"""Revisions of operator messages: rewinding the agent's history to a checkpoint.

Superseded turns, messages and events stay stored for later branching. A rewind restores the
Harness session snapshot taken before the edited message; it never undoes side effects, which the
``history_rewound`` notice lists for the agent.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.agent import proactive
from src.agent.questions import cancel_questions
from src.agent.snapshots import manifest_key
from src.db.engine import Sessions
from src.db.models import (
    PENDING_ACTION_STATUSES,
    Action,
    ActionStatus,
    Chat,
    Message,
    MessageKind,
    MessageStatus,
    Question,
    Turn,
    TurnStatus,
    Wakeup,
)
from src.events import MESSAGES_CHANNEL, NewEvent, append_events, notify
from src.orchestration.models import EditMessage, RewindRequest, Rewound
from src.policy.model import Risk
from src.storage.blobs import BlobStore

MAX_SIDE_EFFECTS = 50
"""Completed side effects listed in the ``history_rewound`` notice."""

_EFFECTS = (ActionStatus.SUCCEEDED, ActionStatus.UNCERTAIN)
_UNFINISHED = (*PENDING_ACTION_STATUSES, ActionStatus.APPROVED)


class _NoCheckpointError(Exception):
    """The snapshot before the edited message is not kept."""


@dataclass
class Rewind:
    """Outcome of a rewind and the actions to cancel once it committed."""

    result: Rewound
    cancelled: list[UUID] = field(default_factory=list)


async def rewind(sessions: Sessions, blobs: BlobStore, request: RewindRequest) -> Rewind:
    """Supersede the turns from the edited message on and queue its revision.

    Operator messages the same turn consumed after the edited one are queued again after the
    revision, in their order; messages of later turns stay superseded. A message that is still
    queued is replaced without a rewind.
    """
    edit = request.edit
    async with sessions.begin() as session:
        chat = await session.get(Chat, request.chat_id, with_for_update=True)
        message = await session.get(Message, edit.message_id, with_for_update=True)
        if chat is None or message is None or message.chat_id != chat.id:
            return Rewind(Rewound(error="message not found"))
        if message.kind is not MessageKind.USER or message.status is MessageStatus.SUPERSEDED:
            return Rewind(Rewound(error="only current operator messages can be revised"))
        if message.turn_id is None:
            revision = _supersede(session, message, edit)
            await _announce(session, message, revision, turns=[], messages=[message.id])
            return Rewind(Rewound(message_id=revision.id))
        turn = await session.get_one(Turn, message.turn_id)
        try:
            chat.snapshot_manifest = await _checkpoint(session, blobs, turn)
        except _NoCheckpointError:
            return Rewind(Rewound(error="the history before this message is not kept"))
        turns = list(
            await session.scalars(
                update(Turn)
                .where(Turn.chat_id == chat.id, Turn.id >= turn.id, Turn.superseded_at.is_(None))
                .values(superseded_at=func.now())
                .returning(Turn.id)
            )
        )
        revision, superseded = await _requeue(session, message, edit, turns)
        side_effects = await _side_effects(session, turns)
        cancelled = list(
            await session.scalars(
                select(Action.id).where(Action.turn_id.in_(turns), Action.status.in_(_UNFINISHED))
            )
        )
        await cancel_questions(session, Question.turn_id.in_(turns))
        await proactive.cancel(
            session,
            chat.id,
            Wakeup.action_id.in_(select(Action.id).where(Action.turn_id.in_(turns))),
        )
        await _announce(
            session, message, revision, turns=turns, messages=superseded, side_effects=side_effects
        )
        notice: dict[str, JsonValue] = {
            "message_id": str(message.id),
            "revision_id": str(revision.id),
            "superseded_turns": len(turns),
            "side_effects_not_rolled_back": side_effects,
        }
        return Rewind(Rewound(message_id=revision.id, notice=notice), cancelled)


async def _checkpoint(session: AsyncSession, blobs: BlobStore, turn: Turn) -> str | None:
    """Return the snapshot the session had before ``turn``; ``None`` is a fresh session.

    Turns finished before checkpoints were recorded still have their snapshot under the derived
    key unless it was pruned. Turns that never reached the model left the session unchanged.

    Raises
    ------
    _NoCheckpointError
        If the snapshot is not kept.
    """
    earlier = await session.scalars(
        select(Turn)
        .where(Turn.chat_id == turn.chat_id, Turn.id < turn.id, Turn.superseded_at.is_(None))
        .order_by(Turn.id.desc())
    )
    for previous in earlier:
        if previous.checkpoint_manifest is not None:
            return previous.checkpoint_manifest
        key = manifest_key(previous.chat_id, previous.id, previous.attempt)
        if await blobs.exists(key):
            return key
        if previous.steps or previous.status is TurnStatus.COMPLETED:
            raise _NoCheckpointError
    return None


async def _requeue(
    session: AsyncSession, message: Message, edit: EditMessage, turns: Sequence[UUID]
) -> tuple[Message, list[UUID]]:
    """Supersede the messages of ``turns``; queue the revision and the input that followed it."""
    consumed = await session.scalars(
        select(Message)
        .where(
            Message.chat_id == message.chat_id,
            Message.turn_id.in_(turns),
            Message.status == MessageStatus.CONSUMED,
        )
        .order_by(Message.id)
        .with_for_update()
    )
    revision = _supersede(session, message, edit)
    superseded = [message.id]
    for item in consumed:
        if item.id == message.id:
            continue
        superseded.append(item.id)
        followed = item.turn_id == message.turn_id and item.id > message.id
        if followed and item.kind is MessageKind.USER:
            _supersede(session, item, None)
        else:
            item.status = MessageStatus.SUPERSEDED
            item.superseded_at = func.now()
    return revision, superseded


def _supersede(session: AsyncSession, message: Message, edit: EditMessage | None) -> Message:
    """Mark ``message`` superseded and queue its revision; ``None`` repeats it unchanged."""
    message.status = MessageStatus.SUPERSEDED
    message.superseded_at = func.now()
    revision = Message(
        id=uuid.uuid7(),
        chat_id=message.chat_id,
        text=message.text,
        image_artifact_ids=message.image_artifact_ids,
        file_artifact_ids=message.file_artifact_ids,
        status=MessageStatus.QUEUED,
        kind=MessageKind.USER,
        revision_of=message.revision_of or message.id,
    )
    if edit is not None:
        revision.text = edit.text if edit.text is not None else message.text
        if edit.image_artifact_ids is not None:
            revision.image_artifact_ids = edit.image_artifact_ids
        if edit.file_artifact_ids is not None:
            revision.file_artifact_ids = edit.file_artifact_ids
    session.add(revision)
    return revision


async def _side_effects(session: AsyncSession, turns: Sequence[UUID]) -> list[JsonValue]:
    rows = await session.execute(
        select(Action.id, Action.tool, Action.status)
        .where(Action.turn_id.in_(turns), Action.status.in_(_EFFECTS), Action.risk != Risk.READ)
        .order_by(Action.id)
        .limit(MAX_SIDE_EFFECTS)
    )
    return [
        {"action_id": str(action_id), "tool": tool, "status": status.value}
        for action_id, tool, status in rows
    ]


async def _announce(  # noqa: PLR0913 - the parts of the event
    session: AsyncSession,
    message: Message,
    revision: Message,
    *,
    turns: Sequence[UUID],
    messages: Sequence[UUID],
    side_effects: Sequence[JsonValue] = (),
) -> None:
    await session.flush()
    await append_events(
        session,
        message.chat_id,
        [
            NewEvent(
                "history_rewound",
                {
                    "message_id": str(message.id),
                    "revision_id": str(revision.id),
                    "superseded_turns": [str(turn_id) for turn_id in turns],
                    "superseded_messages": [str(message_id) for message_id in messages],
                    "side_effects": list(side_effects),
                },
            )
        ],
    )
    await notify(session, MESSAGES_CHANNEL, str(message.chat_id))
