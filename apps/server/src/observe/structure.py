"""Inferred structure of observed content and differences between versions."""

import json
import re

from pydantic import JsonValue

type Schema = dict[str, JsonValue]
"""``{"type": <scalar>}``, ``{"type": "object", "fields": {...}}``,
``{"type": "array", "items": <schema or null>}`` or ``{"anyOf": [...]}``."""

MAX_CHANGES = 50
_PREVIEW_CHARS = 200
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_SCALARS: dict[type, str] = {
    bool: "boolean",
    int: "integer",
    float: "number",
    str: "string",
    type(None): "null",
}


def infer(value: JsonValue) -> Schema:
    """Return the schema of ``value``: fields, types and variants of list items."""
    if isinstance(value, dict):
        return {"type": "object", "fields": {key: infer(item) for key, item in value.items()}}
    if isinstance(value, list):
        items: Schema | None = None
        for item in value:
            schema = infer(item)
            items = schema if items is None else merge(items, schema)
        return {"type": "array", "items": items}
    return {"type": _SCALARS[type(value)]}


def merge(first: Schema, second: Schema) -> Schema:
    """Return a schema both values satisfy; different types become variants."""
    variants = _variants(first)
    for schema in _variants(second):
        for index, known in enumerate(variants):
            if known["type"] == schema["type"]:
                variants[index] = _merge_same(known, schema)
                break
        else:
            variants.append(schema)
    if len(variants) == 1:
        return variants[0]
    return {"anyOf": list[JsonValue](sorted(variants, key=label))}


def _variants(schema: Schema) -> list[Schema]:
    variants = schema.get("anyOf")
    return [v for v in variants if isinstance(v, dict)] if isinstance(variants, list) else [schema]


def _merge_same(first: Schema, second: Schema) -> Schema:
    match first["type"]:
        case "object":
            fields = dict(_fields(first))
            for key, schema in _fields(second).items():
                fields[key] = merge(fields[key], schema) if key in fields else schema
            return {"type": "object", "fields": {**fields}}
        case "array":
            items, other = _items(first), _items(second)
            if items is None or other is None:
                return {"type": "array", "items": items or other}
            return {"type": "array", "items": merge(items, other)}
        case _:
            return first


def _fields(schema: Schema) -> dict[str, Schema]:
    fields = schema.get("fields")
    if not isinstance(fields, dict):
        return {}
    return {key: value for key, value in fields.items() if isinstance(value, dict)}


def _items(schema: Schema) -> Schema | None:
    items = schema.get("items")
    return items if isinstance(items, dict) else None


def label(schema: Schema) -> str:
    """Return the type of ``schema`` as text, e.g. ``string`` or ``null|string``."""
    return "|".join(sorted(str(variant["type"]) for variant in _variants(schema)))


def structural_changes(old: Schema, new: Schema, path: str = "$") -> list[JsonValue]:
    """Return the fields added and removed and the types changed from ``old`` to ``new``.

    The items of an empty list have no known type and match any type.
    """
    if label(old) != label(new):
        return [{"path": path, "change": "type_changed", "from": label(old), "to": label(new)}]
    if old.get("type") == "object":
        old_fields, new_fields = _fields(old), _fields(new)
        changes: list[JsonValue] = []
        for key in sorted(old_fields.keys() | new_fields.keys()):
            child = _child(path, key)
            if key not in new_fields:
                changes.append({"path": child, "change": "removed", "from": label(old_fields[key])})
            elif key not in old_fields:
                changes.append({"path": child, "change": "added", "to": label(new_fields[key])})
            else:
                changes += structural_changes(old_fields[key], new_fields[key], child)
        return changes
    if old.get("type") == "array":
        old_items, new_items = _items(old), _items(new)
        if old_items is not None and new_items is not None:
            return structural_changes(old_items, new_items, f"{path}[*]")
    return []


def value_changes(
    old: JsonValue, new: JsonValue, limit: int = MAX_CHANGES
) -> tuple[list[JsonValue], int]:
    """Return up to ``limit`` value changes from ``old`` to ``new`` and the total count.

    Lists are compared by position, so sort rules keep reordering from showing up as changes.
    Removed and changed entries come in the order of ``old``, then added ones.
    """
    changes: list[JsonValue] = []
    total = 0

    def add(change: dict[str, JsonValue]) -> None:
        nonlocal total
        total += 1
        if len(changes) < limit:
            changes.append(change)

    def walk(before: JsonValue, after: JsonValue, path: str) -> None:
        old, new = _members(before, path), _members(after, path)
        if old is None or new is None or type(before) is not type(after):
            if before != after or type(before) is not type(after):
                add(
                    {"op": "changed", "path": path, "old": _preview(before), "new": _preview(after)}
                )
            return
        for child in old | new:
            if child not in new:
                add({"op": "removed", "path": child, "old": _preview(old[child])})
            elif child not in old:
                add({"op": "added", "path": child, "new": _preview(new[child])})
            else:
                walk(old[child], new[child], child)

    walk(old, new, "$")
    return changes, total


def _members(value: JsonValue, path: str) -> dict[str, JsonValue] | None:
    """Return the entries of a container by their paths: object keys sorted, list items in order."""
    if isinstance(value, dict):
        return {_child(path, key): value[key] for key in sorted(value)}
    if isinstance(value, list):
        return {f"{path}[{index}]": item for index, item in enumerate(value)}
    return None


def _child(path: str, key: str) -> str:
    return f"{path}.{key}" if _IDENTIFIER.fullmatch(key) else f"{path}[{json.dumps(key)}]"


def _preview(value: JsonValue) -> JsonValue:
    """Return scalars as they are and long text or containers shortened to JSON text."""
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False)
    elif isinstance(value, str):
        text = value
    else:
        return value
    return text if len(text) <= _PREVIEW_CHARS else text[:_PREVIEW_CHARS] + "…"
