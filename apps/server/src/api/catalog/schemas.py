"""Catalogs of tools and models chats may use."""

from enum import StrEnum

from pydantic import Field, JsonValue

from src.api.common import Input, ListResult, OffsetQuery, Schema, SortDirection
from src.db import models as db
from src.policy.model import Risk

# --- DATA MODEL --- #


class InputKind(StrEnum):
    """Content a model accepts."""

    TEXT = "text"
    IMAGE = "image"


class Tool(Schema):
    """This object represents a tool the agent may call when one of its groups is enabled."""

    name: str = Field(description="Unique tool name; policy rules match it")
    groups: list[db.ToolGroup] = Field(description="Groups any of which enables the tool")
    risk: Risk = Field(description="Base risk; particular arguments may raise it")
    description: str = Field(description="Description for the model")
    consequences: str = Field(description="What a call does, as shown on approval cards")
    input_schema: JsonValue = Field(description="JSON Schema of the tool arguments")


class LanguageModel(Schema):
    """This object represents a model chats may use."""

    id: str = Field(description="Unique model identifier")
    input: list[InputKind] = Field(description="Accepted input kinds")
    reasoning_levels: list[str] = Field(
        description="Levels a chat may choose; the first is the default"
    )
    pricing_per_mtok: dict[str, float | None] = Field(
        description="USD per million `input`, `output` and `cache_read` tokens"
    )
    default: bool = Field(description="_True_, if new chats use this model")


# --- QUERY PARAMS --- #


class ToolsFilter(Input):
    """Which tools to list."""

    group: list[db.ToolGroup] | None = Field(
        default=None,
        alias="filter[group]",
        description="_Optional_. Filter by one or more `groups` values",
    )


class ToolsSort(StrEnum):
    """Fields tools can be sorted by."""

    NAME = "name"


class ListToolsInput(ToolsFilter, OffsetQuery):
    """This object represents the input used to list tools."""

    sort: ToolsSort | None = Field(
        default=None,
        description="_Optional_. Sort field. Omitted, the catalog order is kept",
    )
    direction: SortDirection = Field(
        default=SortDirection.ASC,
        description="_Optional_. Sort direction. Can be either `asc` or `desc`",
    )


class ModelsSort(StrEnum):
    """Fields models can be sorted by."""

    ID = "id"


class ListModelsInput(OffsetQuery):
    """This object represents the input used to list models."""

    sort: ModelsSort | None = Field(
        default=None,
        description="_Optional_. Sort field. Omitted, the configured order is kept",
    )
    direction: SortDirection = Field(
        default=SortDirection.ASC,
        description="_Optional_. Sort direction. Can be either `asc` or `desc`",
    )


# --- RESULTS --- #


class ListToolsResult(ListResult[Tool]):
    """This object represents the result when tools are listed."""


class ListModelsResult(ListResult[LanguageModel]):
    """This object represents the result when models are listed."""
