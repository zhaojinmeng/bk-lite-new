import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import jsonschema

from apps.cmdb.tests.e2e.contract_manifest import Contract, ContractEntry, ContractManifest, load_manifest

E2E_ROOT = Path(__file__).parent
FIXTURE_ROOT = E2E_ROOT / "fixtures"
SCHEMA_ROOT = E2E_ROOT / "schemas"

REQUIRED = (
    "00_provenance.json",
    "01_source_raw.json",
    "02_prometheus.txt",
    "03_line_protocol.txt",
    "04_vm_response.json",
    "05_expected_cmdb.json",
)
REQUIRED_SCHEMAS = (
    "source.schema.json",
    "vm.schema.json",
    "cmdb.schema.json",
)
PROVENANCE_FIELDS = (
    "source_type",
    "vendor",
    "service",
    "api_operation",
    "api_or_sdk_version",
    "documentation_url",
    "read_at",
    "sanitization",
)

_SCHEMA_TARGETS = (
    ("01_source_raw.json", "source.schema.json"),
    ("04_vm_response.json", "vm.schema.json"),
    ("05_expected_cmdb.json", "cmdb.schema.json"),
)
_SENSITIVE_KEY = re.compile(r"(?:^|_)(?:secret|token|password|api_key)(?:$|_)", re.I)
_SENSITIVE_ASSIGNMENT = re.compile(r"\b(?:secret|token|password|api[_-]?key)\b\s*[:=]\s*['\"]?([^\s,'\"}]+)", re.I,)
_BEARER = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{6,}", re.I)
_PRIVATE_DOMAIN = re.compile(r"\b(?:[a-z0-9-]+\.)+(?:internal|local|corp|lan)\b", re.I)
_HOST_ASSIGNMENT = re.compile(r"\b(?:host|hostname|host_name|node_name|machine_name)\s*=\s*['\"]?([^,\s}'\"]+)", re.I)
_HOST_KEYS = {"host", "hostname", "host_name", "inst_name", "node_name", "machine_name"}
_REDACTED_VALUES = {"***", "redacted", "<redacted>", "[redacted]", "not_applicable"}


class EvidenceValidationError(ValueError):
    """证据包不满足文件、schema、溯源或脱敏契约。"""


@dataclass(frozen=True)
class Evidence:
    case_id: str
    fixture_dir: Path
    schema_dir: Path
    required: tuple[str, ...] = REQUIRED

    @classmethod
    def from_paths(cls, fixture_dir: Path, schema_dir: Path, *, required: tuple[str, ...] = REQUIRED, case_id: str | None = None,) -> "Evidence":
        return cls(case_id=case_id or fixture_dir.name, fixture_dir=fixture_dir, schema_dir=schema_dir, required=required,)

    @property
    def missing_files(self) -> list[str]:
        return [filename for filename in (*self.required, *REQUIRED_SCHEMAS) if not self.path_for(filename).is_file()]

    def path_for(self, filename: str) -> Path:
        root = self.schema_dir if filename in REQUIRED_SCHEMAS else self.fixture_dir
        return root / filename

    def read_json(self, filename: str) -> Any:
        path = self.path_for(filename)
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise EvidenceValidationError(f"{self.case_id}: {filename} 不是可读取的 UTF-8 JSON: {error}") from error

    def validate_complete(self) -> None:
        if self.missing_files:
            missing = ", ".join(self.missing_files)
            raise EvidenceValidationError(f"{self.case_id}: 缺失制品: {missing}")

    def validate_schemas(self) -> None:
        self.validate_complete()
        errors = []
        for document_name, schema_name in _SCHEMA_TARGETS:
            document = self.read_json(document_name)
            schema = self.read_json(schema_name)
            try:
                validator_type = jsonschema.validators.validator_for(schema)
                validator_type.check_schema(schema)
                validator = validator_type(schema)
                validation_errors = sorted(validator.iter_errors(document), key=lambda error: list(error.path))
            except jsonschema.SchemaError as error:
                errors.append(f"{schema_name}: schema 非法: {error.message}")
                continue
            errors.extend(f"{document_name}: {_format_validation_error(error)}" for error in validation_errors)
        for text_name in ("02_prometheus.txt", "03_line_protocol.txt"):
            if not self.path_for(text_name).read_text(encoding="utf-8").strip():
                errors.append(f"{text_name}: 内容不能为空")
        if errors:
            raise EvidenceValidationError(f"{self.case_id}: schema 校验失败: " + "; ".join(errors))

    def validate_provenance(self) -> None:
        self.validate_complete()
        provenance = self.read_json("00_provenance.json")
        if not isinstance(provenance, dict):
            raise EvidenceValidationError(f"{self.case_id}: provenance 必须是对象")
        missing = [field for field in PROVENANCE_FIELDS if field not in provenance]
        if missing:
            raise EvidenceValidationError(f"{self.case_id}: provenance 缺失字段: {', '.join(missing)}")
        invalid = [field for field in PROVENANCE_FIELDS if not isinstance(provenance[field], str) or not provenance[field].strip()]
        if invalid:
            raise EvidenceValidationError(f"{self.case_id}: provenance 字段必须是非空字符串: {', '.join(invalid)}")
        try:
            read_at = datetime.fromisoformat(provenance["read_at"].replace("Z", "+00:00"))
        except ValueError as error:
            raise EvidenceValidationError(f"{self.case_id}: read_at 必须是 ISO-8601 时间") from error
        if read_at.tzinfo is None:
            raise EvidenceValidationError(f"{self.case_id}: read_at 必须包含时区")

    def assert_no_secrets(self) -> None:
        self.validate_complete()
        findings = []
        for filename in self.required:
            path = self.path_for(filename)
            content = path.read_text(encoding="utf-8")
            findings.extend(_scan_text(filename, content))
            if path.suffix == ".json":
                document = self.read_json(filename)
                findings.extend(_scan_json(filename, document))
        if findings:
            raise EvidenceValidationError(f"{self.case_id}: 敏感信息门禁失败: " + "; ".join(sorted(set(findings))))


@dataclass(frozen=True)
class ProductionEvidenceAudit:
    contract_id: Contract
    case_id: str
    missing_files: tuple[str, ...]
    validation_errors: tuple[str, ...] = ()

    @property
    def status(self) -> str:
        if self.missing_files:
            return "missing_evidence"
        if self.validation_errors:
            return "invalid_evidence"
        return "ready"


@dataclass(frozen=True)
class NonProductionEvidenceAudit:
    contract_id: Contract
    case_id: str
    status: str
    reason: str | None


@dataclass(frozen=True)
class ManifestEvidenceAudit:
    production: tuple[ProductionEvidenceAudit, ...]
    non_production: tuple[NonProductionEvidenceAudit, ...]

    @property
    def incomplete_production(self) -> tuple[ProductionEvidenceAudit, ...]:
        return tuple(item for item in self.production if item.status != "ready")

    def to_dict(self) -> dict[str, list[dict[str, Any]]]:
        return {
            "production": [asdict(item) | {"status": item.status} for item in self.production],
            "non_production": [asdict(item) for item in self.non_production],
        }


def load_evidence(case_id: str, root: Path | str | None = None) -> Evidence:
    evidence_root = E2E_ROOT if root is None else Path(root)
    return Evidence.from_paths(evidence_root / "fixtures" / case_id, evidence_root / "schemas" / case_id, required=REQUIRED, case_id=case_id,)


def audit_manifest_evidence(
    manifest: ContractManifest | None = None, *, root: Path | str | None = None, archive_declaration: Path | str | None = None,
) -> ManifestEvidenceAudit:
    manifest = manifest or load_manifest()
    evidence_root = E2E_ROOT if root is None else Path(root)
    archive_path = evidence_root / "fixtures" / "_task4_archived_summary.json" if archive_declaration is None else Path(archive_declaration)
    archive_reasons = _load_archive_reasons(archive_path) if manifest.non_production_entries else {}
    production = tuple(_audit_production_entry(entry, evidence_root) for entry in manifest.production_entries)
    non_production = tuple(
        NonProductionEvidenceAudit(
            contract_id=entry.contract_id,
            case_id=entry.case_id,
            status="archived" if entry.case_id in archive_reasons else "missing_archive_declaration",
            reason=archive_reasons.get(entry.case_id),
        )
        for entry in manifest.non_production_entries
    )
    return ManifestEvidenceAudit(production=production, non_production=non_production)


def _audit_production_entry(entry: ContractEntry, evidence_root: Path) -> ProductionEvidenceAudit:
    evidence = load_evidence(entry.case_id, root=evidence_root)
    missing_files = tuple(evidence.missing_files)
    validation_errors = []
    if not missing_files:
        for validate in (
            evidence.validate_schemas,
            evidence.validate_provenance,
            evidence.assert_no_secrets,
        ):
            try:
                validate()
            except EvidenceValidationError as error:
                validation_errors.append(str(error))
    return ProductionEvidenceAudit(
        contract_id=entry.contract_id, case_id=entry.case_id, missing_files=missing_files, validation_errors=tuple(validation_errors),
    )


def _format_validation_error(error: jsonschema.ValidationError) -> str:
    path = ".".join(str(item) for item in error.absolute_path) or "<root>"
    return f"{path}: {error.message}"


def _load_archive_reasons(path: Path) -> dict[str, str]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        objects = document["objects"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise EvidenceValidationError(f"归档声明不可读取: {path}: {error}") from error
    if not isinstance(objects, list):
        raise EvidenceValidationError(f"归档声明 objects 必须是列表: {path}")
    reasons: dict[str, str] = {}
    for item in objects:
        if not isinstance(item, dict):
            raise EvidenceValidationError(f"归档声明条目必须是对象: {path}")
        case_id = item.get("model_id")
        reason = item.get("placeholder_reason")
        if not isinstance(case_id, str) or not isinstance(reason, str) or not reason:
            raise EvidenceValidationError(f"归档声明条目缺少 model_id/placeholder_reason: {path}")
        if case_id in reasons:
            raise EvidenceValidationError(f"归档声明 case_id 重复: {case_id}")
        reasons[case_id] = reason
    return reasons


def _scan_json(filename: str, value: Any, path: tuple[str, ...] = ()) -> list[str]:
    findings = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = (*path, str(key))
            if _SENSITIVE_KEY.search(str(key)) and not _is_redacted(child):
                findings.append(f"{filename}:{'.'.join(child_path)}")
            if str(key).lower() in _HOST_KEYS and _is_unredacted_hostname(child):
                findings.append(f"{filename}:{'.'.join(child_path)}={child}")
            findings.extend(_scan_json(filename, child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            findings.extend(_scan_json(filename, child, (*path, str(index))))
    return findings


def _scan_text(filename: str, content: str) -> Iterable[str]:
    for match in _BEARER.finditer(content):
        yield f"{filename}:{match.group(0)}"
    for match in _PRIVATE_DOMAIN.finditer(content):
        yield f"{filename}:{match.group(0)}"
    for match in _SENSITIVE_ASSIGNMENT.finditer(content):
        if match.group(1).lower() not in _REDACTED_VALUES:
            yield f"{filename}:{match.group(0)}"
    for match in _HOST_ASSIGNMENT.finditer(content):
        if _is_unredacted_hostname(match.group(1)):
            yield f"{filename}:{match.group(0)}"


def _is_redacted(value: Any) -> bool:
    return isinstance(value, str) and value.strip().lower() in _REDACTED_VALUES


def _is_unredacted_hostname(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip() or _is_redacted(value):
        return False
    lowered = value.lower()
    return not any(reserved in lowered for reserved in ("example.invalid", "example.com", "example.net", "example.org"))
