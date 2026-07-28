import json
from pathlib import Path

import jsonschema
import pytest

from apps.cmdb.tests.e2e.boundary_schema_drift import BOUNDARY_CASES


E2E_ROOT = Path(__file__).parent
SCALAR_DRIFT_TYPES = {
    "string",
    "number",
    "integer",
    "boolean",
    "null",
}


def _item_schema(case_id):
    schema = json.loads(
        (
            E2E_ROOT / "schemas" / case_id / "source.schema.json"
        ).read_text(encoding="utf-8")
    )
    result_schema = schema["properties"]["result"]
    model_id = result_schema["required"][0]
    return schema, result_schema["properties"][model_id]["items"]


@pytest.mark.parametrize("case_id", sorted(BOUNDARY_CASES))
def test_boundary_source_schema约束已知字段并显式声明新增字段漂移策略(case_id):
    _, item_schema = _item_schema(case_id)

    assert item_schema["x-drift-policy"] == {
        "unknown_scalar_fields": "report_non_blocking",
        "unknown_nested_fields": "reject",
    }
    assert set(item_schema["required"]) <= set(item_schema["properties"])
    assert all(
        "type" in item_schema["properties"][field]
        for field in item_schema["required"]
    )
    assert all(
        "const" not in item_schema["properties"][field]
        for field in item_schema["required"]
    )
    assert set(item_schema["additionalProperties"]["type"]) == (
        SCALAR_DRIFT_TYPES
    )


@pytest.mark.parametrize("case_id", sorted(BOUNDARY_CASES))
def test_boundary_source_schema允许新增标量进入drift但拒绝未知嵌套结构(case_id):
    schema, _ = _item_schema(case_id)
    source = json.loads(
        (
            E2E_ROOT / "fixtures" / case_id / "01_source_raw.json"
        ).read_text(encoding="utf-8")
    )
    result_key = next(iter(source["result"]))
    with_scalar_drift = json.loads(json.dumps(source))
    with_scalar_drift["result"][result_key][0]["vendor_new_field"] = "new"
    with_nested_drift = json.loads(json.dumps(source))
    with_nested_drift["result"][result_key][0]["vendor_new_nested"] = {
        "unexpected": True
    }
    validator = jsonschema.Draft202012Validator(schema)

    assert list(validator.iter_errors(with_scalar_drift)) == []
    assert list(validator.iter_errors(with_nested_drift))
