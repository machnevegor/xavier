"""Policy rules: global and per-chat guardrails."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import Field

from src.api.common import Input, ListResult, PageQuery, Result, Schema, SortDirection
from src.policy.model import Effect

# --- DATA MODEL --- #


class PolicyRule(Schema):
    """This object represents a policy rule."""

    id: UUID = Field(description="Unique rule identifier")
    tool_pattern: str = Field(description="Shell-style pattern matched against the tool name")
    resource_pattern: str | None = Field(
        description="Shell-style pattern matched against hosts, URLs and paths; *null* matches any"
    )
    chat_id: UUID | None = Field(
        description="Unique chat identifier; *null* applies the rule to every chat"
    )
    effect: Effect = Field(description="*allow*, *ask* or *deny*")
    priority: int = Field(description="Higher priority wins when several rules match")
    description: str = Field(description="Why the rule exists")
    created_at: datetime = Field(description="Date and time of rule creation")
    updated_at: datetime = Field(description="Date and time of last rule update")
    updated_by: UUID | None = Field(description="User who last changed the rule")


# --- QUERY PARAMS --- #


class PolicyRulesFilter(Input):
    """Which policy rules to list."""

    chat_id: list[UUID] | None = Field(
        default=None,
        alias="filter[chat_id]",
        description="_Optional_. Filter by one or more `chat_id` values",
    )
    is_global: bool | None = Field(
        default=None,
        alias="filter[is_global]",
        description="_Optional_. _True_ for rules of every chat, _False_ for rules of one chat",
    )
    effect: list[Effect] | None = Field(
        default=None,
        alias="filter[effect]",
        description="_Optional_. Filter by one or more `effect` values",
    )


class PolicyRulesSort(StrEnum):
    """Fields policy rules can be sorted by."""

    ID = "id"
    PRIORITY = "priority"
    CREATED_AT = "created_at"
    UPDATED_AT = "updated_at"


class ListPolicyRulesInput(PolicyRulesFilter, PageQuery):
    """This object represents the input used to list policy rules."""

    sort: PolicyRulesSort = Field(
        default=PolicyRulesSort.CREATED_AT, description="_Optional_. Sort field"
    )
    direction: SortDirection = Field(
        default=SortDirection.DESC,
        description="_Optional_. Sort direction. Can be either `asc` or `desc`",
    )


# --- INPUTS --- #


class CreatePolicyRuleInput(Input):
    """This object represents the input used to create the policy rule.

    Patterns are shell-style (`*`, `?`, `[...]`).
    """

    tool_pattern: str = Field(min_length=1, description="Shell-style pattern of the tool name")
    resource_pattern: str | None = Field(
        default=None,
        description="_Optional_. Shell-style pattern of hosts, URLs and paths; omitted matches "
        "any call",
    )
    chat_id: UUID | None = Field(
        default=None,
        description="_Optional_. Chat the rule applies to; omitted applies it to every chat",
    )
    effect: Effect = Field(description="*allow*, *ask* or *deny*")
    priority: int = Field(default=0, description="_Optional_. Higher priority wins")
    description: str = Field(default="", description="_Optional_. Why the rule exists")


class UpdatePolicyRuleInput(Input):
    """This object represents the input used to update the policy rule.

    Omitted fields keep their values; explicit *null* clears `resource_pattern` and makes the rule
    global with `chat_id`.
    """

    tool_pattern: str | None = Field(
        default=None, min_length=1, description="_Optional_. Shell-style pattern of the tool name"
    )
    resource_pattern: str | None = Field(
        default=None,
        description="_Optional_. Shell-style pattern of hosts, URLs and paths; *null* matches "
        "any call",
    )
    chat_id: UUID | None = Field(
        default=None,
        description="_Optional_. Chat the rule applies to; *null* applies it to every chat",
    )
    effect: Effect | None = Field(default=None, description="_Optional_. *allow*, *ask* or *deny*")
    priority: int | None = Field(default=None, description="_Optional_. Higher priority wins")
    description: str | None = Field(default=None, description="_Optional_. Why the rule exists")


# --- ARGS --- #


class CreatePolicyRuleArgs(Input):
    """This object represents the arguments used to create the policy rule."""

    data: CreatePolicyRuleInput


class UpdatePolicyRuleArgs(Input):
    """This object represents the arguments used to update the policy rule."""

    data: UpdatePolicyRuleInput


# --- RESULTS --- #


class CreatePolicyRuleResult(Result[PolicyRule]):
    """This object represents the result when the policy rule is created."""


class ListPolicyRulesResult(ListResult[PolicyRule]):
    """This object represents the result when policy rules are listed."""


class ReadPolicyRuleResult(Result[PolicyRule]):
    """This object represents the result when the policy rule is read."""


class UpdatePolicyRuleResult(Result[PolicyRule]):
    """This object represents the result when the policy rule is updated."""


class DeletePolicyRuleResult(Result[PolicyRule]):
    """This object represents the result when the policy rule is deleted."""
