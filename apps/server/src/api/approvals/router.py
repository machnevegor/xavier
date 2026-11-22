"""Actions waiting for a decision of the user, and the decisions.

An action waits when the policy asks for approval or when the agent requests a capability or
a credential. Only actions of the user's chats that are not deleted are listed and can be
decided; another user's action does not exist.
"""

from typing import Annotated
from uuid import UUID

from fastapi import Depends, Query
from sqlalchemy import select

from src.agent.gateway import Actions
from src.api.actions.schemas import Action
from src.api.approvals.schemas import (
    ApprovalsSort,
    DecideActionArgs,
    DecideActionInput,
    DecideActionResult,
    DecideActionsArgs,
    DecideActionsResult,
    ListApprovalsInput,
    ListApprovalsResult,
)
from src.api.common import CONFLICT, UNAVAILABLE, paginate, router
from src.api.dependencies import CurrentUserDep, OwnedChatDep, Services, ServicesDep, SessionDep
from src.db import models as db
from src.errors import AlreadyDecidedError, NotFoundError

router = router("", "approvals")

_SORT = {ApprovalsSort.ID: db.Action.id, ApprovalsSort.CREATED_AT: db.Action.created_at}


async def get_owned_action(
    action_id: UUID, principal: CurrentUserDep, session: SessionDep
) -> db.Action:
    """Return the action of one of the user's chats."""
    action = await session.scalar(
        select(db.Action)
        .join(db.Chat, db.Chat.id == db.Action.chat_id)
        .where(
            db.Action.id == action_id,
            db.Chat.created_by == principal.user_id,
            db.Chat.deleted_at.is_(None),
        )
    )
    if action is None:
        raise NotFoundError(f"action {action_id} not found")
    return action


OwnedActionDep = Annotated[db.Action, Depends(get_owned_action)]


@router.get("/approvals")
async def list_approvals(
    principal: CurrentUserDep, session: SessionDep, params: Annotated[ListApprovalsInput, Query()]
) -> ListApprovalsResult:
    """List the user's actions waiting for a decision, oldest first by default."""
    query = (
        select(db.Action)
        .join(db.Chat, db.Chat.id == db.Action.chat_id)
        .where(
            db.Action.status.in_(db.PENDING_ACTION_STATUSES),
            db.Chat.created_by == principal.user_id,
            db.Chat.deleted_at.is_(None),
        )
    )
    if params.chat_id:
        query = query.where(db.Action.chat_id.in_(params.chat_id))
    if params.tool:
        query = query.where(db.Action.tool.in_(params.tool))
    rows, info = await paginate(
        session,
        query,
        params,
        sort=_SORT[params.sort],
        direction=params.direction,
        key=db.Action.id,
    )
    return ListApprovalsResult(data=[Action.model_validate(row) for row in rows], info=info)


@router.post("/approvals/{action_id}/decision", responses={409: CONFLICT, 503: UNAVAILABLE})
async def decide_action(
    action: OwnedActionDep, body: DecideActionArgs, services: ServicesDep
) -> DecideActionResult:
    """Approve, reject or cancel an action waiting for a decision.

    An approval with `scope` *chat* or *always* also creates allow rules for later calls of the
    tool on the action's resources at `breadth`. Approving a `request_capability` action enables
    the capability in its chat; rejecting it records the decline, and later requests for it are
    refused until the user changes that setting.

    Repeating the same decision is safe and returns 200. A different decision on an action
    that is already decided, or on one that no longer waits, returns 409.
    A 503 means the decision is stored but the agent was not notified; repeat the request.
    """
    decided = await _decide(services, action.id, body.data)
    await services.commands.action_decided(decided.chat_id, decided.id)
    return DecideActionResult(data=Action.model_validate(decided))


@router.post("/chats/{chat_id}/approvals/decisions", responses={409: CONFLICT, 503: UNAVAILABLE})
async def decide_actions(
    chat_id: UUID,
    _: OwnedChatDep,
    session: SessionDep,
    body: DecideActionsArgs,
    services: ServicesDep,
) -> DecideActionsResult:
    """Decide several actions of the chat at once, each with its own decision and scope.

    Items are applied in order with the rules of the single-action endpoint. An item that fails
    ends the request with its error; the items before it stay decided and the agent learns of
    them, so repeating the request is safe.
    """
    found = set(
        await session.scalars(
            select(db.Action.id).where(
                db.Action.chat_id == chat_id,
                db.Action.id.in_([item.action_id for item in body.data]),
            )
        )
    )
    for item in body.data:
        if item.action_id not in found:
            raise NotFoundError(f"action {item.action_id} not found in chat {chat_id}")
    decided: list[Action] = []
    for item in body.data:
        action = await _decide(services, item.action_id, item)
        await services.commands.action_decided(chat_id, action.id)
        decided.append(Action.model_validate(action))
    return DecideActionsResult(data=decided)


async def _decide(services: Services, action_id: UUID, body: DecideActionInput) -> db.Action:
    action, decided = await Actions(services.sessions).decide(
        action_id, body.decision, body.scope, body.breadth
    )
    if not decided and action.decision is not body.decision:
        raise AlreadyDecidedError(f"action {action_id} is already decided or no longer waits")
    return action
