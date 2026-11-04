from __future__ import annotations

import pytest

from harness_core import schema
from harness_core.errors import SchemaError

S = {
    "type": "object",
    "required": ["id", "n"],
    "properties": {
        "id": {"type": "string"},
        "n": {"type": "integer", "minimum": 1},
        "v": {"enum": ["A", "B"]},
        "xs": {"type": "array", "items": {"type": "string"}},
        "opt": {"type": ["string", "null"]},
    },
    "additionalProperties": False,
}


def test_valid_instance_passes():
    schema.validate({"id": "u1", "n": 2, "v": "A", "xs": ["a"], "opt": None}, S)


@pytest.mark.parametrize(
    "bad",
    [
        {"n": 1},  # missing required
        {"id": 1, "n": 1},  # wrong type
        {"id": "u", "n": 0},  # below minimum
        {"id": "u", "n": True},  # bool is not an integer
        {"id": "u", "n": 1, "v": "C"},  # not in enum
        {"id": "u", "n": 1, "xs": [1]},  # bad item
        {"id": "u", "n": 1, "extra": 1},  # additionalProperties false
        [],  # not an object
    ],
)
def test_invalid_instances_raise(bad):
    with pytest.raises(SchemaError):
        schema.validate(bad, S)


def test_additional_properties_as_schema():
    s = {"type": "object", "additionalProperties": {"type": "integer"}}
    schema.validate({"a": 1}, s)
    with pytest.raises(SchemaError):
        schema.validate({"a": "x"}, s)


@pytest.mark.parametrize(
    "name", ["event", "ledger_row", "verdict_row", "scope_row", "outputs_row", "turns", "receipt", "pins"]
)
def test_run_schemas_load(name):
    s = schema.load(name)
    assert s["type"] == "object"
