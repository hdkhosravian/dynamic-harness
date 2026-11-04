"""Subset JSON-Schema validator: type, required, properties, enum, items,
additionalProperties, minimum. Raises SchemaError on the first violation."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from .errors import SchemaError

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "schema" / "run"

_TYPES = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}


def validate(instance, schema: dict, path: str = "$") -> None:
    t = schema.get("type")
    if t is not None:
        types = t if isinstance(t, list) else [t]
        if not any(_TYPES[x](instance) for x in types):
            raise SchemaError(f"{path}: expected {t}, got {type(instance).__name__}")
    if "enum" in schema and instance not in schema["enum"]:
        raise SchemaError(f"{path}: {instance!r} not in {schema['enum']}")
    if "minimum" in schema and _TYPES["number"](instance) and instance < schema["minimum"]:
        raise SchemaError(f"{path}: {instance} < minimum {schema['minimum']}")
    if isinstance(instance, dict):
        props = schema.get("properties", {})
        for k in schema.get("required", []):
            if k not in instance:
                raise SchemaError(f"{path}: missing required {k!r}")
        extra = schema.get("additionalProperties", True)
        for k, v in instance.items():
            if k in props:
                validate(v, props[k], f"{path}.{k}")
            elif extra is False:
                raise SchemaError(f"{path}: unexpected property {k!r}")
            elif isinstance(extra, dict):
                validate(v, extra, f"{path}.{k}")
    if isinstance(instance, list) and "items" in schema:
        for i, v in enumerate(instance):
            validate(v, schema["items"], f"{path}[{i}]")


@lru_cache(maxsize=None)
def load(name: str) -> dict:
    return json.loads((SCHEMA_DIR / f"{name}.json").read_text("utf-8"))


def check(instance, name: str) -> None:
    validate(instance, load(name), name)
