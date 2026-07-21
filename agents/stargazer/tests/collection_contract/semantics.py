import ast
import re
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable

_METRIC_NAME = re.compile(r"[a-zA-Z_:][a-zA-Z0-9_:]*\Z")
_LABEL_NAME = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]*")
_INTEGER = re.compile(r"-?[0-9]+\Z")
_UNSIGNED_INTEGER = re.compile(r"[0-9]+\Z")


class SemanticParseError(ValueError):
    """严格语义解析失败，错误信息始终包含格式与行号。"""


@dataclass(frozen=True)
class PrometheusSample:
    metric_name: str
    labels: tuple[tuple[str, str], ...]
    numeric_value: Decimal
    timestamp_ms: int
    field_name: str = field(compare=False, hash=False, repr=False)
    numeric_kind: str = field(compare=False, hash=False, repr=False)


@dataclass(frozen=True)
class TypedField:
    kind: str
    value: object


@dataclass(frozen=True)
class LineProtocolRecord:
    measurement: str
    tags: tuple[tuple[str, str], ...]
    typed_fields: tuple[tuple[str, TypedField], ...]
    timestamp_ns: int


def _error(protocol: str, line_number: int, line: str, reason: str):
    raise SemanticParseError(f"{protocol} line {line_number}: {reason}: {line!r}")


def _find_prometheus_label_end(line: str, start: int) -> int:
    escaped = False
    in_quotes = False
    for index in range(start, len(line)):
        char = line[index]
        if escaped:
            escaped = False
            continue
        if char == "\\" and in_quotes:
            escaped = True
            continue
        if char == '"':
            in_quotes = not in_quotes
            continue
        if char == "}" and not in_quotes:
            return index
    return -1


def _parse_prometheus_labels(
    raw_labels: str, line_number: int, line: str
) -> tuple[tuple[str, str], ...]:
    labels = {}
    index = 0
    while index < len(raw_labels):
        while index < len(raw_labels) and raw_labels[index].isspace():
            index += 1
        match = _LABEL_NAME.match(raw_labels, index)
        if not match:
            _error("Prometheus", line_number, line, "invalid label name")
        key = match.group(0)
        if key in labels:
            _error("Prometheus", line_number, line, f"duplicate label {key!r}")
        index = match.end()
        while index < len(raw_labels) and raw_labels[index].isspace():
            index += 1
        if index >= len(raw_labels) or raw_labels[index] != "=":
            _error("Prometheus", line_number, line, "label is missing '='")
        index += 1
        while index < len(raw_labels) and raw_labels[index].isspace():
            index += 1
        if index >= len(raw_labels) or raw_labels[index] != '"':
            _error("Prometheus", line_number, line, "label value must be quoted")
        index += 1

        value = []
        closed = False
        while index < len(raw_labels):
            char = raw_labels[index]
            if char == '"':
                index += 1
                closed = True
                break
            if char == "\\":
                index += 1
                if index >= len(raw_labels):
                    _error("Prometheus", line_number, line, "dangling label escape")
                escaped = raw_labels[index]
                escape_values = {'"': '"', "\\": "\\", "n": "\n"}
                if escaped not in escape_values:
                    _error(
                        "Prometheus",
                        line_number,
                        line,
                        f"unsupported label escape \\{escaped}",
                    )
                value.append(escape_values[escaped])
            else:
                value.append(char)
            index += 1
        if not closed:
            _error("Prometheus", line_number, line, "unterminated label value")
        labels[key] = "".join(value)

        while index < len(raw_labels) and raw_labels[index].isspace():
            index += 1
        if index == len(raw_labels):
            break
        if raw_labels[index] != ",":
            _error("Prometheus", line_number, line, "labels must be comma-separated")
        index += 1
        if index == len(raw_labels):
            _error("Prometheus", line_number, line, "trailing label comma")

    return tuple(sorted(labels.items()))


def parse_prometheus(text: str) -> Counter[PrometheusSample]:
    records: Counter[PrometheusSample] = Counter()
    metric_types = {}
    current_type = None
    for line_number, raw_line in enumerate(str(text).splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("# TYPE "):
            type_parts = line.split()
            if len(type_parts) >= 4:
                metric_types[type_parts[2]] = type_parts[3]
                current_type = type_parts[3]
            continue
        if line.startswith("#"):
            continue

        labels: tuple[tuple[str, str], ...] = ()
        if "{" in line:
            label_start = line.find("{")
            metric_name = line[:label_start].strip()
            label_end = _find_prometheus_label_end(line, label_start + 1)
            if label_end < 0:
                _error("Prometheus", line_number, line, "unterminated label block")
            labels = _parse_prometheus_labels(
                line[label_start + 1 : label_end], line_number, line
            )
            value_and_timestamp = line[label_end + 1 :].strip()
        else:
            parts = line.split(maxsplit=1)
            if len(parts) != 2:
                _error("Prometheus", line_number, line, "missing value and timestamp")
            metric_name, value_and_timestamp = parts

        if not _METRIC_NAME.fullmatch(metric_name):
            _error("Prometheus", line_number, line, "invalid metric name")
        parts = value_and_timestamp.split()
        if len(parts) != 2:
            _error(
                "Prometheus",
                line_number,
                line,
                "expected exactly one value and one millisecond timestamp",
            )
        value_token, timestamp_token = parts
        try:
            value = Decimal(value_token)
        except InvalidOperation:
            _error("Prometheus", line_number, line, "invalid numeric value")
        if not value.is_finite():
            _error("Prometheus", line_number, line, "non-finite numeric value")
        if len(timestamp_token) != 13 or not _UNSIGNED_INTEGER.fullmatch(
            timestamp_token
        ):
            _error("Prometheus", line_number, line, "invalid millisecond timestamp")

        records[
            PrometheusSample(
                metric_name=metric_name,
                labels=labels,
                numeric_value=value,
                timestamp_ms=int(timestamp_token),
                field_name=metric_types.get(metric_name, current_type or "value"),
                numeric_kind=(
                    "float"
                    if "." in value_token or "e" in value_token.lower()
                    else "integer"
                ),
            )
        ] += 1
    return records


def _split_unescaped(
    text: str, delimiter: str, *, maxsplit: int = -1, quoted: bool = False
) -> list[str]:
    result = []
    start = 0
    splits = 0
    escaped = False
    in_quotes = False
    for index, char in enumerate(text):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if quoted and char == '"':
            in_quotes = not in_quotes
            continue
        if char == delimiter and not in_quotes and (maxsplit < 0 or splits < maxsplit):
            result.append(text[start:index])
            start = index + 1
            splits += 1
    result.append(text[start:])
    return result


def _unescape_line_identifier(
    value: str, line_number: int, line: str, kind: str
) -> str:
    output = []
    index = 0
    while index < len(value):
        char = value[index]
        if char != "\\":
            output.append(char)
            index += 1
            continue
        index += 1
        if index >= len(value):
            _error("Line Protocol", line_number, line, f"dangling {kind} escape")
        output.append(value[index])
        index += 1
    decoded = "".join(output)
    if not decoded:
        _error("Line Protocol", line_number, line, f"empty {kind}")
    return decoded


def _parse_string_field(raw_value: str, line_number: int, line: str) -> str:
    if len(raw_value) < 2 or raw_value[-1] != '"':
        _error("Line Protocol", line_number, line, "unterminated string field")
    output = []
    index = 1
    while index < len(raw_value) - 1:
        char = raw_value[index]
        if char == '"':
            _error(
                "Line Protocol",
                line_number,
                line,
                "unescaped quote inside string field",
            )
        if char != "\\":
            output.append(char)
            index += 1
            continue
        index += 1
        if index >= len(raw_value) - 1:
            _error("Line Protocol", line_number, line, "dangling string escape")
        escaped = raw_value[index]
        if escaped not in {'"', "\\"}:
            _error(
                "Line Protocol",
                line_number,
                line,
                f"unsupported string escape \\{escaped}",
            )
        output.append(escaped)
        index += 1
    return "".join(output)


def _parse_typed_field(raw_value: str, line_number: int, line: str) -> TypedField:
    if raw_value.startswith('"'):
        return TypedField("string", _parse_string_field(raw_value, line_number, line))
    lowered = raw_value.lower()
    if lowered in {"t", "true"}:
        return TypedField("boolean", True)
    if lowered in {"f", "false"}:
        return TypedField("boolean", False)
    if raw_value.endswith("i"):
        integer = raw_value[:-1]
        if not _INTEGER.fullmatch(integer):
            _error("Line Protocol", line_number, line, "invalid integer field")
        return TypedField("integer", int(integer))
    if raw_value.endswith("u"):
        integer = raw_value[:-1]
        if not _UNSIGNED_INTEGER.fullmatch(integer):
            _error("Line Protocol", line_number, line, "invalid unsigned field")
        return TypedField("unsigned", int(integer))
    try:
        numeric = Decimal(raw_value)
    except InvalidOperation:
        _error("Line Protocol", line_number, line, "invalid field value")
    if not numeric.is_finite():
        _error("Line Protocol", line_number, line, "non-finite numeric field")
    return TypedField("float", numeric)


def _parse_line_protocol_record(line: str, line_number: int) -> LineProtocolRecord:
    sections = _split_unescaped(line, " ", maxsplit=2, quoted=True)
    if len(sections) != 3 or any(not section for section in sections):
        _error(
            "Line Protocol",
            line_number,
            line,
            "expected measurement/tags, fields, and nanosecond timestamp",
        )
    measurement_and_tags, raw_fields, timestamp_token = sections
    if len(timestamp_token) != 19 or not _UNSIGNED_INTEGER.fullmatch(timestamp_token):
        _error("Line Protocol", line_number, line, "invalid nanosecond timestamp")

    head_parts = _split_unescaped(measurement_and_tags, ",")
    measurement = _unescape_line_identifier(
        head_parts[0], line_number, line, "measurement"
    )
    tags = {}
    for raw_tag in head_parts[1:]:
        pair = _split_unescaped(raw_tag, "=", maxsplit=1)
        if len(pair) != 2:
            _error("Line Protocol", line_number, line, "invalid tag")
        key = _unescape_line_identifier(pair[0], line_number, line, "tag key")
        value = _unescape_line_identifier(pair[1], line_number, line, "tag value")
        if key in tags:
            _error("Line Protocol", line_number, line, f"duplicate tag {key!r}")
        tags[key] = value

    fields = {}
    for raw_field in _split_unescaped(raw_fields, ",", quoted=True):
        pair = _split_unescaped(raw_field, "=", maxsplit=1, quoted=True)
        if len(pair) != 2:
            _error("Line Protocol", line_number, line, "invalid field")
        key = _unescape_line_identifier(pair[0], line_number, line, "field key")
        if key in fields:
            _error("Line Protocol", line_number, line, f"duplicate field {key!r}")
        fields[key] = _parse_typed_field(pair[1], line_number, line)
    if not fields:
        _error("Line Protocol", line_number, line, "missing fields")

    return LineProtocolRecord(
        measurement=measurement,
        tags=tuple(sorted(tags.items())),
        typed_fields=tuple(sorted(fields.items())),
        timestamp_ns=int(timestamp_token),
    )


def parse_line_protocol(lines: str | Iterable[str],) -> Counter[LineProtocolRecord]:
    raw_lines = lines.splitlines() if isinstance(lines, str) else lines
    records: Counter[LineProtocolRecord] = Counter()
    for line_number, raw_line in enumerate(raw_lines, start=1):
        line = str(raw_line).strip()
        if not line or line.startswith("#"):
            continue
        records[_parse_line_protocol_record(line, line_number)] += 1
    return records


def assert_timestamp_propagation(
    prometheus_records: Counter[PrometheusSample],
    line_protocol_records: Counter[LineProtocolRecord],
) -> None:
    """按指标身份验证毫秒时间戳被传播为纳秒，而非只比较时间戳集合。"""
    common_tag_keys = {
        "agent_id",
        "instance_id",
        "instance_type",
        "collect_type",
        "config_type",
    }
    unmatched_lines = list(line_protocol_records.elements())

    for sample in prometheus_records.elements():
        identity_labels = tuple(
            (key, value) for key, value in sample.labels if key not in common_tag_keys
        )
        expected_typed_field = TypedField(
            sample.numeric_kind,
            (
                sample.numeric_value
                if sample.numeric_kind == "float"
                else int(sample.numeric_value)
            ),
        )
        expected_fields = ((sample.field_name, expected_typed_field),)
        identity_matches = [
            record
            for record in unmatched_lines
            if record.measurement == sample.metric_name
            and record.typed_fields == expected_fields
            and all(
                dict(record.tags).get(key) == value for key, value in identity_labels
            )
        ]
        if not identity_matches:
            raise AssertionError(
                "timestamp propagation missing Line Protocol identity for "
                f"{sample.metric_name}{identity_labels!r}"
            )

        expected_timestamp_ns = sample.timestamp_ms * 1_000_000
        timestamp_match = next(
            (
                record
                for record in identity_matches
                if record.timestamp_ns == expected_timestamp_ns
            ),
            None,
        )
        if timestamp_match is None:
            actual_timestamps = sorted(
                record.timestamp_ns for record in identity_matches
            )
            raise AssertionError(
                "timestamp propagation mismatch for "
                f"{sample.metric_name}{identity_labels!r}: "
                f"expected {expected_timestamp_ns}, got {actual_timestamps}"
            )
        unmatched_lines.remove(timestamp_match)

    if unmatched_lines:
        raise AssertionError(
            "timestamp propagation found unmatched Line Protocol records: "
            f"{unmatched_lines!r}"
        )


def find_legacy_vm_helper_calls(source: str) -> list[int]:
    helper_name = "step2_" + "push_to_vm"
    tree = ast.parse(source)
    helper_aliases = {helper_name}

    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        for imported_name in node.names:
            if imported_name.name == helper_name:
                helper_aliases.add(imported_name.asname or imported_name.name)

    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            value = node.value
            aliases_helper = (
                isinstance(value, ast.Name) and value.id in helper_aliases
            ) or (isinstance(value, ast.Attribute) and value.attr == helper_name)
            if not aliases_helper:
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and target.id not in helper_aliases:
                    helper_aliases.add(target.id)
                    changed = True

    return sorted(
        {
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and (
                (isinstance(node.func, ast.Name) and node.func.id in helper_aliases)
                or (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr == helper_name
                )
            )
        }
    )


def find_legacy_vm_helper_violations(contract_dir: Path) -> dict[str, list[int]]:
    violations = {}
    for contract_path in contract_dir.rglob("*.py"):
        line_numbers = find_legacy_vm_helper_calls(
            contract_path.read_text(encoding="utf-8")
        )
        if line_numbers:
            violations[str(contract_path.relative_to(contract_dir))] = line_numbers
    return violations
