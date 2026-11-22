"""Catalogs of what chats can use: the agent's tools and the configured models.

A chat offers the agent the tools of its `tool_groups`; `files` comes with `execute` or
`compute`. Policy rules match tools by `name`. A chat's `model` is one of the listed models, and
its `reasoning` one of the model's `reasoning_levels`.
"""

from collections.abc import Callable
from typing import Annotated

from fastapi import Query

from src.api.catalog.schemas import (
    InputKind,
    LanguageModel,
    ListModelsInput,
    ListModelsResult,
    ListToolsInput,
    ListToolsResult,
    ModelsSort,
    Tool,
    ToolsSort,
)
from src.api.common import SortDirection, paginate_items, router
from src.api.dependencies import CurrentUserDep, ServicesDep
from src.tools.registry import default_registry, input_schema

router = router("", "catalog")


def _ordered[T](
    items: list[T], *, key: Callable[[T], str] | None, direction: SortDirection
) -> list[T]:
    """Return ``items`` in ``direction``, by ``key`` when one is set, else in their given order."""
    ranked = list(enumerate(items))
    descending = direction is SortDirection.DESC
    ranked.sort(
        key=lambda pair: ((key(pair[1]),) if key is not None else ()) + (pair[0],),
        reverse=descending,
    )
    return [item for _, item in ranked]


@router.get("/tools")
async def list_tools(
    _: CurrentUserDep, params: Annotated[ListToolsInput, Query()]
) -> ListToolsResult:
    """List the tools with their groups, base risk and argument schema."""
    items = [
        Tool(
            name=tool.name,
            groups=sorted(tool.groups),
            risk=tool.risk,
            description=tool.description,
            consequences=tool.consequences,
            input_schema=input_schema(tool.args_type),
        )
        for tool in default_registry().all()
        if not params.group or tool.groups & set(params.group)
    ]
    items = _ordered(
        items,
        key=(lambda tool: tool.name) if params.sort is ToolsSort.NAME else None,
        direction=params.direction,
    )
    data, info = paginate_items(items, params)
    return ListToolsResult(data=data, info=info)


@router.get("/models")
async def list_models(
    _: CurrentUserDep, services: ServicesDep, params: Annotated[ListModelsInput, Query()]
) -> ListModelsResult:
    """List the models chats may use; `default` marks the model of new chats."""
    agent = services.settings.agent
    items = [
        LanguageModel(
            id=model.id,
            input=[InputKind(kind) for kind in model.input],
            reasoning_levels=list(model.reasoning_levels),
            pricing_per_mtok=model.pricing_per_mtok.model_dump(),
            default=model.id == agent.default_model,
        )
        for model in agent.models
    ]
    items = _ordered(
        items,
        key=(lambda model: model.id) if params.sort is ModelsSort.ID else None,
        direction=params.direction,
    )
    data, info = paginate_items(items, params)
    return ListModelsResult(data=data, info=info)
