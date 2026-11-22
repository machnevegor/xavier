"""Policy rules decide whether a tool call runs, asks the user or is denied.

A rule matches a call when `tool_pattern` matches the tool name and `resource_pattern`, if set,
matches one of the call's hosts, URLs or paths; patterns are shell-style (`*`, `?`, `[...]`).
A rule with `chat_id` applies to that chat only, a global rule to every chat of the user.

Among matching rules the highest `priority` wins, then the more specific rule: a chat rule, one
with a resource pattern, a longer tool pattern. On a full tie *deny* beats *ask* beats *allow*.
Without a matching rule the chat's mode decides. Forbidden calls are always denied. Rules apply
to tool calls made after they are stored.
"""

import uuid
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Query, status
from sqlalchemy import or_, select

from src.api.common import paginate, router
from src.api.dependencies import CurrentUserDep, SessionDep, chat_visible
from src.api.policy.schemas import (
    CreatePolicyRuleArgs,
    CreatePolicyRuleResult,
    DeletePolicyRuleResult,
    ListPolicyRulesInput,
    ListPolicyRulesResult,
    PolicyRule,
    PolicyRulesSort,
    ReadPolicyRuleResult,
    UpdatePolicyRuleArgs,
    UpdatePolicyRuleResult,
)
from src.db import models as db
from src.errors import NotFoundError

router = router("/policy/rules", "policy")

SORT = {
    PolicyRulesSort.ID: db.PolicyRule.id,
    PolicyRulesSort.PRIORITY: db.PolicyRule.priority,
    PolicyRulesSort.CREATED_AT: db.PolicyRule.created_at,
    PolicyRulesSort.UPDATED_AT: db.PolicyRule.updated_at,
}


async def get_owned_rule(
    rule_id: UUID, principal: CurrentUserDep, session: SessionDep
) -> db.PolicyRule:
    """Return the user's rule; one of a deleted chat does not exist."""
    rule = await session.get(db.PolicyRule, rule_id)
    if (
        rule is None
        or rule.created_by != principal.user_id
        or not await chat_visible(session, rule.chat_id, principal.user_id)
    ):
        raise NotFoundError(f"rule {rule_id} not found")
    return rule


OwnedRuleDep = Annotated[db.PolicyRule, Depends(get_owned_rule)]


@router.get("")
async def list_policy_rules(
    principal: CurrentUserDep, session: SessionDep, params: Annotated[ListPolicyRulesInput, Query()]
) -> ListPolicyRulesResult:
    """List the user's policy rules.

    The Guardrails tab of a chat lists `filter[chat_id]`, the global page `filter[is_global]=true`.
    """
    query = (
        select(db.PolicyRule)
        .outerjoin(db.Chat, db.Chat.id == db.PolicyRule.chat_id)
        .where(
            db.PolicyRule.created_by == principal.user_id,
            or_(db.PolicyRule.chat_id.is_(None), db.Chat.deleted_at.is_(None)),
        )
    )
    if params.chat_id:
        query = query.where(db.PolicyRule.chat_id.in_(params.chat_id))
    if params.is_global is not None:
        query = query.where(
            db.PolicyRule.chat_id.is_(None)
            if params.is_global
            else db.PolicyRule.chat_id.is_not(None)
        )
    if params.effect:
        query = query.where(db.PolicyRule.effect.in_(params.effect))
    rows, info = await paginate(
        session,
        query,
        params,
        sort=SORT[params.sort],
        direction=params.direction,
        key=db.PolicyRule.id,
    )
    return ListPolicyRulesResult(data=[PolicyRule.model_validate(row) for row in rows], info=info)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_policy_rule(
    body: CreatePolicyRuleArgs, principal: CurrentUserDep, session: SessionDep
) -> CreatePolicyRuleResult:
    """Add a rule."""
    data = body.data
    if not await chat_visible(session, data.chat_id, principal.user_id):
        raise NotFoundError(f"chat {data.chat_id} not found")
    rule = db.PolicyRule(
        id=uuid.uuid7(),
        created_by=principal.user_id,
        tool_pattern=data.tool_pattern,
        resource_pattern=data.resource_pattern,
        chat_id=data.chat_id,
        effect=data.effect,
        priority=data.priority,
        description=data.description,
        updated_by=principal.user_id,
    )
    session.add(rule)
    await session.flush()
    await session.refresh(rule)
    return CreatePolicyRuleResult(data=PolicyRule.model_validate(rule))


@router.get("/{rule_id}")
async def read_policy_rule(rule: OwnedRuleDep) -> ReadPolicyRuleResult:
    """Return a rule."""
    return ReadPolicyRuleResult(data=PolicyRule.model_validate(rule))


@router.patch("/{rule_id}")
async def update_policy_rule(
    body: UpdatePolicyRuleArgs,
    rule: OwnedRuleDep,
    principal: CurrentUserDep,
    session: SessionDep,
) -> UpdatePolicyRuleResult:
    """Change a rule."""
    data = body.data
    fields = data.model_fields_set
    if "chat_id" in fields:
        if not await chat_visible(session, data.chat_id, rule.created_by):
            raise NotFoundError(f"chat {data.chat_id} not found")
        rule.chat_id = data.chat_id
    if "resource_pattern" in fields:
        rule.resource_pattern = data.resource_pattern
    rule.tool_pattern = data.tool_pattern or rule.tool_pattern
    rule.effect = data.effect or rule.effect
    rule.priority = rule.priority if data.priority is None else data.priority
    rule.description = rule.description if data.description is None else data.description
    rule.updated_by = principal.user_id
    await session.flush()
    await session.refresh(rule)
    return UpdatePolicyRuleResult(data=PolicyRule.model_validate(rule))


@router.delete("/{rule_id}")
async def delete_policy_rule(rule: OwnedRuleDep, session: SessionDep) -> DeletePolicyRuleResult:
    """Delete a rule for good."""
    deleted = PolicyRule.model_validate(rule)
    await session.delete(rule)
    return DeletePolicyRuleResult(data=deleted)
