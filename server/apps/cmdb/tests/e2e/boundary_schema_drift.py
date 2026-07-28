import json
from pathlib import Path
from typing import Iterable

import jsonschema


E2E_ROOT = Path(__file__).parent
BOUNDARY_CASES = frozenset(
    {
        "disk",
        "gpu",
        "hbase",
        "host_physcial_server",
        "iis",
        "keepalived",
        "memory",
        "mssql",
        "network",
        "nic",
        "openresty",
        "oracle",
        "physcial_server",
        "rocketmq",
        "spark",
        "vmware_vc",
    }
)
GENERATED_FROM = {
    "evidence_version": 1,
    "source_artifact": "fixtures/<case_id>/01_source_raw.json",
    "schema_artifact": "schemas/<case_id>/source.schema.json",
}


class BoundarySchemaDriftError(ValueError):
    """边界 schema 或源证据存在阻断性漂移。"""


def audit_boundary_schema_drift(
    *,
    root: Path = E2E_ROOT,
    case_ids: Iterable[str] = BOUNDARY_CASES,
) -> dict:
    cases = []
    for case_id in sorted(case_ids):
        source = _read_json(
            root / "fixtures" / case_id / "01_source_raw.json", case_id
        )
        schema = _read_json(
            root / "schemas" / case_id / "source.schema.json", case_id
        )
        item_schema = _item_schema(schema, case_id)
        _validate_declared_fields(item_schema, case_id)
        _validate_document(source, schema, case_id)
        unknown_fields = _unknown_optional_fields(source, item_schema, case_id)
        cases.append(
            {
                "case_id": case_id,
                "unknown_optional_fields": sorted(unknown_fields),
            }
        )
    return {
        "generated_from": GENERATED_FROM,
        "cases": cases,
    }


def _read_json(path: Path, case_id: str) -> dict:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise BoundarySchemaDriftError(
            f"{case_id}: 无法读取 drift 输入 {path}: {error}"
        ) from error
    if not isinstance(document, dict):
        raise BoundarySchemaDriftError(f"{case_id}: drift 输入必须是 JSON 对象")
    return document


def _item_schema(schema: dict, case_id: str) -> dict:
    try:
        result_schema = schema["properties"]["result"]
        model_ids = result_schema["required"]
        if len(model_ids) != 1:
            raise KeyError("result.required")
        return result_schema["properties"][model_ids[0]]["items"]
    except (KeyError, TypeError) as error:
        raise BoundarySchemaDriftError(
            f"{case_id}: source schema 缺少唯一 result 模型的 items 定义"
        ) from error


def _validate_declared_fields(item_schema: dict, case_id: str) -> None:
    properties = item_schema.get("properties")
    required = item_schema.get("required")
    if not isinstance(properties, dict) or not isinstance(required, list):
        raise BoundarySchemaDriftError(
            f"{case_id}: items 必须声明 properties 和 required"
        )
    undeclared = sorted(set(required) - set(properties))
    if undeclared:
        raise BoundarySchemaDriftError(
            f"{case_id}: required 字段未在 properties 声明: {undeclared}"
        )


def _validate_document(source: dict, schema: dict, case_id: str) -> None:
    try:
        validator_type = jsonschema.validators.validator_for(schema)
        validator_type.check_schema(schema)
        errors = sorted(
            validator_type(schema).iter_errors(source),
            key=lambda error: list(error.absolute_path),
        )
    except jsonschema.SchemaError as error:
        raise BoundarySchemaDriftError(
            f"{case_id}: source schema 非法: {error.message}"
        ) from error
    if errors:
        details = "; ".join(error.message for error in errors)
        raise BoundarySchemaDriftError(
            f"{case_id}: source 证据存在阻断性漂移: {details}"
        )


def _unknown_optional_fields(
    source: dict, item_schema: dict, case_id: str
) -> set[str]:
    properties = set(item_schema["properties"])
    try:
        result = source["result"]
        model_ids = list(result)
        if len(model_ids) != 1:
            raise KeyError("result")
        records = result[model_ids[0]]
        if not isinstance(records, list):
            raise KeyError("records")
    except (KeyError, TypeError) as error:
        raise BoundarySchemaDriftError(
            f"{case_id}: source 证据缺少唯一 result 记录列表"
        ) from error
    unknown = set()
    for record in records:
        if not isinstance(record, dict):
            raise BoundarySchemaDriftError(
                f"{case_id}: source result 记录必须是对象"
            )
        unknown.update(set(record) - properties)
    return unknown
