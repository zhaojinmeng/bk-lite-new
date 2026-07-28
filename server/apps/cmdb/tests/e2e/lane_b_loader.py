import json
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import jsonschema

from apps.cmdb.tests.e2e.contract_manifest import ContractEntry, load_manifest
from apps.cmdb.tests.e2e.contract_loader import Evidence


E2E_ROOT = Path(__file__).parent
LANE_B_FILES = ("04_vm_response.json", "05_expected_cmdb.json")
LANE_B_SCHEMAS = ("vm.schema.json", "cmdb.schema.json")
_LP_TIMESTAMP = re.compile(r"[0-9]{19}\Z")


class LaneBValidationError(ValueError):
    """VM→CMDB 静态证据不满足 Lane B 契约。"""


def _split_unescaped(
    value: str, delimiter: str, *, maxsplit: int = -1
) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    escaped = False
    splits = 0
    for character in value:
        if escaped:
            current.extend(("\\", character))
            escaped = False
            continue
        if character == "\\":
            escaped = True
            continue
        if character == delimiter and (maxsplit < 0 or splits < maxsplit):
            parts.append("".join(current))
            current = []
            splits += 1
            continue
        current.append(character)
    if escaped:
        raise LaneBValidationError("Line Protocol 存在悬空转义")
    parts.append("".join(current))
    return parts


def _lp_unescape(value: str) -> str:
    output: list[str] = []
    index = 0
    while index < len(value):
        if value[index] != "\\":
            output.append(value[index])
            index += 1
            continue
        index += 1
        if index >= len(value):
            raise LaneBValidationError("Line Protocol 存在悬空转义")
        output.append(value[index])
        index += 1
    return "".join(output)


def _lp_field_value(raw_value: str) -> str:
    lowered = raw_value.lower()
    if lowered in {"true", "t"}:
        return "true"
    if lowered in {"false", "f"}:
        return "false"
    if raw_value.endswith(("i", "u")):
        integer = raw_value[:-1]
        try:
            return str(int(integer))
        except ValueError as error:
            raise LaneBValidationError(
                f"Line Protocol 整数字段非法: {raw_value}"
            ) from error
    if raw_value.startswith('"') and raw_value.endswith('"'):
        return _lp_unescape(raw_value[1:-1])
    try:
        return str(Decimal(raw_value))
    except InvalidOperation as error:
        raise LaneBValidationError(
            f"Line Protocol 字段值非法: {raw_value}"
        ) from error


def build_vm_response_from_line_protocol(text: str) -> dict[str, Any]:
    result = []
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        sections = _split_unescaped(line, " ", maxsplit=2)
        if len(sections) != 3:
            raise LaneBValidationError(
                f"Line Protocol 第 {line_number} 行必须包含标签、字段和纳秒时间"
            )
        head, raw_fields, timestamp_token = sections
        if not _LP_TIMESTAMP.fullmatch(timestamp_token):
            raise LaneBValidationError(
                f"Line Protocol 第 {line_number} 行时间戳不是19位纳秒"
            )
        head_parts = _split_unescaped(head, ",")
        measurement = _lp_unescape(head_parts[0])
        tags: dict[str, str] = {}
        for raw_tag in head_parts[1:]:
            pair = _split_unescaped(raw_tag, "=", maxsplit=1)
            if len(pair) != 2:
                raise LaneBValidationError(
                    f"Line Protocol 第 {line_number} 行标签非法: {raw_tag}"
                )
            key, value = map(_lp_unescape, pair)
            if key in tags:
                raise LaneBValidationError(
                    f"Line Protocol 第 {line_number} 行标签重复: {key}"
                )
            tags[key] = value
        timestamp_seconds = Decimal(timestamp_token) / Decimal(1_000_000_000)
        timestamp: int | float = (
            int(timestamp_seconds)
            if timestamp_seconds == timestamp_seconds.to_integral()
            else float(timestamp_seconds)
        )
        for raw_field in _split_unescaped(raw_fields, ","):
            pair = _split_unescaped(raw_field, "=", maxsplit=1)
            if len(pair) != 2:
                raise LaneBValidationError(
                    f"Line Protocol 第 {line_number} 行字段非法: {raw_field}"
                )
            field_name = _lp_unescape(pair[0])
            result.append(
                {
                    "metric": {
                        **tags,
                        "__name__": f"{measurement}_{field_name}",
                    },
                    "value": [timestamp, _lp_field_value(pair[1])],
                }
            )
    if not result:
        raise LaneBValidationError("Line Protocol 没有可转换记录")
    return {
        "status": "success",
        "data": {"resultType": "vector", "result": result},
    }


def build_case_vm_schema(
    vm_response: dict[str, Any],
    *,
    emitted_model_id: str | None = None,
    target_metric: dict[str, str] | None = None,
) -> dict[str, Any]:
    rows = vm_response["data"]["result"]
    if target_metric is None:
        matching_rows = [
            row
            for row in rows
            if emitted_model_id is None
            or row["metric"].get("model_id") == emitted_model_id
            or row["metric"].get("bk_obj_id") == emitted_model_id
        ]
        discriminator_keys: tuple[str, ...] = ()
    else:
        discriminator_keys = tuple(
            key
            for key in ("__name__", "instance_id")
            if key in target_metric
        )
        for candidate in (
            "resource_id",
            "pid",
            "inst_name",
            "resource_name",
            "ip_addr",
            "self_device",
            "data_dir",
            "port",
            "name",
        ):
            matching_rows = [
                row
                for row in rows
                if all(
                    row["metric"].get(key) == target_metric[key]
                    for key in discriminator_keys
                )
            ]
            if len(matching_rows) == 1:
                break
            if candidate in target_metric:
                discriminator_keys += (candidate,)
        else:
            matching_rows = [
                row
                for row in rows
                if all(
                    row["metric"].get(key) == target_metric[key]
                    for key in discriminator_keys
                )
            ]
    if len(matching_rows) != 1:
        raise LaneBValidationError(
            f"{emitted_model_id or '<single>'}: VM 响应必须精确包含一条目标模型记录，"
            f"实际 {len(matching_rows)} 条"
        )
    target_metric = matching_rows[0]["metric"]
    core_identity_keys = tuple(
        dict.fromkeys(
            key
            for key in (
            "__name__",
            "model_id",
            "bk_obj_id",
            "instance_id",
            "agent_id",
            "config_type",
            "collect_status",
        )
            if key in target_metric
        )
    )
    target_identity_keys = tuple(
        dict.fromkeys((*core_identity_keys, *discriminator_keys))
    )
    row_schema = {
        "type": "object",
        "required": ["metric", "value"],
        "additionalProperties": False,
        "properties": {
            "metric": {
                "type": "object",
                "required": list(core_identity_keys),
                "properties": {
                    key: {"type": "string"} for key in core_identity_keys
                },
                "additionalProperties": {"type": "string"},
            },
            "value": {
                "type": "array",
                "minItems": 2,
                "maxItems": 2,
                "prefixItems": [
                    {
                        "type": "number",
                        "minimum": 1_000_000_000,
                        "maximum": 4_102_444_800,
                    },
                    {"type": "string"},
                ],
                "items": False,
            },
        },
    }
    target_row_schema = {
        **row_schema,
        "properties": {
            **row_schema["properties"],
            "metric": {
                **row_schema["properties"]["metric"],
                "required": list(target_identity_keys),
                "properties": {
                    **row_schema["properties"]["metric"]["properties"],
                    **{
                        key: {"const": target_metric[key]}
                        for key in target_identity_keys
                    },
                },
            },
        },
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "required": ["status", "data"],
        "additionalProperties": False,
        "properties": {
            "status": {"const": "success"},
            "data": {
                "type": "object",
                "required": ["resultType", "result"],
                "additionalProperties": False,
                "properties": {
                    "resultType": {"const": "vector"},
                    "result": {
                        "type": "array",
                        "minItems": 1,
                        "items": row_schema,
                        "contains": target_row_schema,
                        "minContains": 1,
                        "maxContains": 1,
                    },
                },
            },
        },
    }


def _json_shape_schema(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return {
            "type": "object",
            "required": list(value),
            "additionalProperties": False,
            "properties": {
                key: _json_shape_schema(item) for key, item in value.items()
            },
        }
    if isinstance(value, list):
        if not value:
            return {"type": "array", "maxItems": 0}
        return {
            "type": "array",
            "minItems": len(value),
            "maxItems": len(value),
            "prefixItems": [_json_shape_schema(item) for item in value],
            "items": False,
        }
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, int):
        return {"type": "integer"}
    if isinstance(value, float):
        return {"type": "number"}
    if value is None:
        return {"type": "null"}
    return {"type": "string"}


def build_case_cmdb_schema(expected: dict[str, Any]) -> dict[str, Any]:
    schema = _json_shape_schema(expected)
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["properties"]["model_id"] = {"const": expected["model_id"]}
    if "expected_instances" in expected:
        schema["properties"]["expected_instances"]["minItems"] = 1
    return schema


@dataclass(frozen=True)
class LaneBEvidence:
    case_id: str
    fixture_dir: Path
    schema_dir: Path

    @property
    def missing_files(self) -> tuple[str, ...]:
        paths = {
            **{name: self.fixture_dir / name for name in LANE_B_FILES},
            **{name: self.schema_dir / name for name in LANE_B_SCHEMAS},
        }
        return tuple(name for name, path in paths.items() if not path.is_file())

    def read_json(self, filename: str) -> Any:
        root = self.schema_dir if filename in LANE_B_SCHEMAS else self.fixture_dir
        try:
            return json.loads((root / filename).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise LaneBValidationError(
                f"{self.case_id}: {filename} 不是可读取的 UTF-8 JSON: {error}"
            ) from error

    def validate(self) -> None:
        if self.missing_files:
            raise LaneBValidationError(
                f"{self.case_id}: 缺失 Lane B 制品: {', '.join(self.missing_files)}"
            )
        for document_name, schema_name in zip(LANE_B_FILES, LANE_B_SCHEMAS):
            document = self.read_json(document_name)
            schema = self.read_json(schema_name)
            try:
                jsonschema.validators.validator_for(schema).check_schema(schema)
                jsonschema.validate(document, schema)
            except jsonschema.ValidationError as error:
                path = ".".join(str(item) for item in error.absolute_path) or "<root>"
                raise LaneBValidationError(
                    f"{self.case_id}: {document_name} schema 失败: {path}: {error.message}"
                ) from error
        Evidence.from_paths(
            self.fixture_dir,
            self.schema_dir,
            case_id=self.case_id,
        ).assert_no_secrets()


@dataclass(frozen=True)
class ModelFieldDefinition:
    attr_type: str
    is_required: bool
    enum_values: tuple[Any, ...] = ()


def parse_model_field_definitions(
    rows: Any, *, model_id: str
) -> dict[str, ModelFieldDefinition]:
    row_iter = iter(rows)
    try:
        next(row_iter)
        headers = tuple(next(row_iter))
        indexes = {
            name: headers.index(name)
            for name in ("attr_id", "attr_type", "is_required", "option")
        }
    except (StopIteration, ValueError) as error:
        raise LaneBValidationError(
            f"{model_id}: 生产模型 attr sheet 缺少两行表头或字段定义列"
        ) from error

    required_index = max(indexes.values())
    fields: dict[str, ModelFieldDefinition] = {}
    for row_number, row in enumerate(row_iter, start=3):
        row = tuple(row)
        if not any(cell not in (None, "") for cell in row):
            continue
        if len(row) <= required_index:
            raise LaneBValidationError(
                f"{model_id}: 生产模型 attr sheet 第 {row_number} 行非空但列数不足"
            )
        attr_id = row[indexes["attr_id"]]
        attr_type = row[indexes["attr_type"]]
        if attr_id in (None, "") or attr_type in (None, ""):
            raise LaneBValidationError(
                f"{model_id}: 生产模型 attr sheet 第 {row_number} 行缺少 attr_id/attr_type"
            )
        enum_values: tuple[Any, ...] = ()
        option = row[indexes["option"]]
        if attr_type == "enum" and option not in (None, ""):
            try:
                choices = json.loads(str(option))
                if isinstance(choices, list):
                    enum_values = tuple(choice["id"] for choice in choices)
                elif (
                    isinstance(choices, dict)
                    and choices.get("enum_rule_type") == "public_library"
                    and choices.get("public_library_id")
                ):
                    enum_values = ()
                else:
                    raise TypeError("未知 enum option 形态")
            except (json.JSONDecodeError, KeyError, TypeError) as error:
                raise LaneBValidationError(
                    f"{model_id}: 生产模型字段 {attr_id} enum option 非法"
                ) from error
        fields[str(attr_id)] = ModelFieldDefinition(
            attr_type=str(attr_type),
            is_required=row[indexes["is_required"]] is True,
            enum_values=enum_values,
        )
    return fields


def parse_model_field_rows(
    rows: Any, *, model_id: str
) -> dict[str, str]:
    return {
        field: definition.attr_type
        for field, definition in parse_model_field_definitions(
            rows, model_id=model_id
        ).items()
    }


def load_lane_b_evidence(
    case_id: str, root: Path | str | None = None
) -> LaneBEvidence:
    e2e_root = E2E_ROOT if root is None else Path(root)
    return LaneBEvidence(
        case_id=case_id,
        fixture_dir=e2e_root / "fixtures" / case_id,
        schema_dir=e2e_root / "schemas" / case_id,
    )


def lane_b_entries() -> tuple[ContractEntry, ...]:
    return tuple(entry for entry in load_manifest().validation_entries if entry.lane_b)


def lane_b_incomplete(
    root: Path | str | None = None,
) -> dict[str, tuple[str, ...]]:
    return {
        entry.case_id: evidence.missing_files
        for entry in lane_b_entries()
        if (
            evidence := load_lane_b_evidence(entry.case_id, root=root)
        ).missing_files
    }
