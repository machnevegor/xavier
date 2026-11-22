"""Catalog list order, without the stack."""

from types import SimpleNamespace
from typing import cast

import pytest
from pydantic import ValidationError

from src.api.catalog.router import _ordered, list_models, list_tools
from src.api.catalog.schemas import ListModelsInput, ListToolsInput
from src.api.common import SortDirection
from src.api.dependencies import Services
from src.auth.service import Principal
from src.db.models import ToolGroup


class _Price:
    def model_dump(self) -> dict[str, float | None]:
        return {"input": 1.0, "output": 2.0, "cache_read": None}


class _Model:
    def __init__(self, model_id: str) -> None:
        self.id = model_id
        self.input = ["text"]
        self.reasoning_levels = ["off"]
        self.pricing_per_mtok = _Price()


def _services() -> SimpleNamespace:
    return SimpleNamespace(
        settings=SimpleNamespace(
            agent=SimpleNamespace(models=[_Model("b"), _Model("a")], default_model="b")
        )
    )


def test_equal_keys_keep_their_original_order() -> None:
    assert _ordered(["b", "a", "b"], key=lambda item: item, direction=SortDirection.ASC) == [
        "a",
        "b",
        "b",
    ]
    assert _ordered(["b", "a", "b"], key=None, direction=SortDirection.DESC) == ["b", "a", "b"]


async def test_tools_keep_catalog_order_until_asked_to_sort() -> None:
    principal = cast(Principal, None)
    default = (await list_tools(principal, ListToolsInput())).data
    names = [tool.name for tool in default]
    named = (await list_tools(principal, ListToolsInput(sort="name"))).data
    reverse = (
        await list_tools(principal, ListToolsInput(sort="name", direction=SortDirection.DESC))
    ).data
    execute = (
        await list_tools(principal, ListToolsInput.model_validate({"filter[group]": ["execute"]}))
    ).data

    assert names[0] == "web_fetch"
    assert [tool.name for tool in named] == sorted(names)
    assert [tool.name for tool in reverse] == sorted(names, reverse=True)
    assert {tool.name for tool in execute} == {"code_run", "job"}
    assert all(ToolGroup.EXECUTE in tool.groups for tool in execute)


async def test_models_keep_configured_order_until_sorted_by_id() -> None:
    principal = cast(Principal, None)
    services = cast(Services, _services())
    default = (await list_models(principal, services, ListModelsInput())).data
    by_id = (await list_models(principal, services, ListModelsInput(sort="id"))).data

    assert [model.id for model in default] == ["b", "a"]
    assert [model.id for model in by_id] == ["a", "b"]
    assert default[0].default is True


def test_catalog_query_rejects_unknown_sort_and_direction() -> None:
    with pytest.raises(ValidationError):
        ListToolsInput(sort="nope")
    with pytest.raises(ValidationError):
        ListModelsInput(direction="sideways")
