import json
import ipaddress
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit

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
LANE_A_REQUIRED = (
    "00_provenance.json",
    "01_source_raw.json",
    "02_prometheus.txt",
    "03_line_protocol.txt",
)
REQUIRED_SCHEMAS = (
    "source.schema.json",
    "vm.schema.json",
    "cmdb.schema.json",
)
LANE_A_REQUIRED_SCHEMAS = ("source.schema.json",)
PROVENANCE_FIELDS = (
    "source_type",
    "source_kind",
    "vendor",
    "service",
    "api_operation",
    "api_or_sdk_version",
    "documentation_url",
    "read_at",
    "sanitization",
)
SOURCE_TYPE_SANITIZED_REAL_ENVIRONMENT = "sanitized_real_environment"
SOURCE_TYPE_OFFICIAL_CLOUD_API_DOCUMENTATION = "official_cloud_api_documentation"
SOURCE_TYPES = {
    SOURCE_TYPE_SANITIZED_REAL_ENVIRONMENT,
    SOURCE_TYPE_OFFICIAL_CLOUD_API_DOCUMENTATION,
}
SOURCE_KINDS = {
    "docker_real",
    "official_sdk_mock",
    "boundary_mock",
    "private_api_mock",
}
_NOT_APPLICABLE = "not_applicable"
_REAL_ENVIRONMENT_NOT_APPLICABLE_FIELDS = (
    "service",
    "api_operation",
    "api_or_sdk_version",
    "documentation_url",
)
_OFFICIAL_DOCUMENTATION_HOSTS = {
    "qcloud": frozenset({"cloud.tencent.com", "intl.cloud.tencent.com"}),
    "tencentcloud": frozenset({"cloud.tencent.com", "intl.cloud.tencent.com"}),
    "aliyun": frozenset({"help.aliyun.com", "www.alibabacloud.com"}),
    "alibaba_cloud": frozenset({"help.aliyun.com", "www.alibabacloud.com"}),
    "hwcloud": frozenset({"developer.huaweicloud.com", "support.huaweicloud.com"}),
    "huawei_cloud": frozenset({"developer.huaweicloud.com", "support.huaweicloud.com"}),
    "fusioninsight": frozenset({"support.huawei.com", "support.huaweicloud.com"}),
    "oceanstor": frozenset({"support.huawei.com"}),
    "h3c_cas": frozenset({"www.h3c.com"}),
    "zstack": frozenset({"www.zstack.io"}),
}

_SCHEMA_TARGETS = (
    ("01_source_raw.json", "source.schema.json"),
    ("04_vm_response.json", "vm.schema.json"),
    ("05_expected_cmdb.json", "cmdb.schema.json"),
)
_ASSIGNMENT = re.compile(r"(?<![A-Za-z0-9_-])['\"]?(?P<key>[A-Za-z][A-Za-z0-9_-]*)['\"]?\s*[:=]\s*['\"]?(?P<value>[^\s,'\"}]+)", re.I,)
_BEARER = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{6,}", re.I)
_PRIVATE_DOMAIN = re.compile(r"\b(?:[a-z0-9-]+\.)+(?:internal|local|corp|lan)\b", re.I)
_HOST_ASSIGNMENT = re.compile(r"\b(?:host|hostname|host_name|inst_name|node_name|machine_name)\s*=\s*['\"]?([^,\s}'\"]+)", re.I)
_HOST_KEYS = {"host", "hostname", "instname", "nodename", "machinename"}
_SENSITIVE_KEY_MARKERS = ("secret", "token", "password", "apikey")
_REDACTED_VALUES = {"***", "redacted", "<redacted>", "[redacted]", "not_applicable"}


class EvidenceValidationError(ValueError):
    """证据包不满足文件、schema、溯源或脱敏契约。"""


@dataclass(frozen=True)
class Evidence:
    case_id: str
    fixture_dir: Path
    schema_dir: Path
    required: tuple[str, ...] = REQUIRED
    required_schemas: tuple[str, ...] = REQUIRED_SCHEMAS

    @classmethod
    def from_paths(
        cls,
        fixture_dir: Path,
        schema_dir: Path,
        *,
        required: tuple[str, ...] = REQUIRED,
        required_schemas: tuple[str, ...] = REQUIRED_SCHEMAS,
        case_id: str | None = None,
    ) -> "Evidence":
        return cls(
            case_id=case_id or fixture_dir.name, fixture_dir=fixture_dir, schema_dir=schema_dir, required=required, required_schemas=required_schemas,
        )

    @property
    def missing_files(self) -> list[str]:
        return [filename for filename in (*self.required, *self.required_schemas) if not self.path_for(filename).is_file()]

    def path_for(self, filename: str) -> Path:
        root = self.schema_dir if filename in self.required_schemas else self.fixture_dir
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
            if schema_name not in self.required_schemas:
                continue
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
        _validate_provenance_source(self.case_id, provenance)

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
class ValidationEvidenceAudit:
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
    validation: tuple[ValidationEvidenceAudit, ...]
    non_production: tuple[NonProductionEvidenceAudit, ...]

    @property
    def incomplete_validation(self) -> tuple[ValidationEvidenceAudit, ...]:
        return tuple(item for item in self.validation if item.status != "ready")

    def to_dict(self) -> dict[str, list[dict[str, Any]]]:
        return {
            "validation": [asdict(item) | {"status": item.status} for item in self.validation],
            "non_production": [asdict(item) for item in self.non_production],
        }

    def ready_by_source_kind(
        self, *, root: Path | str | None = None
    ) -> dict[str, tuple[str, ...]]:
        """按证据来源分栏，且只统计已通过全部 Lane A 门禁的 case。"""

        evidence_root = E2E_ROOT if root is None else Path(root)
        grouped: dict[str, list[str]] = {kind: [] for kind in sorted(SOURCE_KINDS)}
        for item in self.validation:
            if item.status != "ready":
                continue
            provenance = load_lane_a_evidence(
                item.case_id, root=evidence_root
            ).read_json("00_provenance.json")
            grouped[provenance["source_kind"]].append(item.case_id)
        return {
            kind: tuple(sorted(case_ids)) for kind, case_ids in grouped.items()
        }


def load_evidence(case_id: str, root: Path | str | None = None) -> Evidence:
    evidence_root = E2E_ROOT if root is None else Path(root)
    return Evidence.from_paths(evidence_root / "fixtures" / case_id, evidence_root / "schemas" / case_id, required=REQUIRED, case_id=case_id,)


def load_lane_a_evidence(case_id: str, root: Path | str | None = None) -> Evidence:
    evidence_root = E2E_ROOT if root is None else Path(root)
    return Evidence.from_paths(
        evidence_root / "fixtures" / case_id,
        evidence_root / "schemas" / case_id,
        required=LANE_A_REQUIRED,
        required_schemas=LANE_A_REQUIRED_SCHEMAS,
        case_id=case_id,
    )


def audit_manifest_evidence(
    manifest: ContractManifest | None = None, *, root: Path | str | None = None, archive_declaration: Path | str | None = None,
) -> ManifestEvidenceAudit:
    manifest = manifest or load_manifest()
    evidence_root = E2E_ROOT if root is None else Path(root)
    archive_path = evidence_root / "fixtures" / "_task4_archived_summary.json" if archive_declaration is None else Path(archive_declaration)
    archive_reasons = _load_archive_reasons(archive_path) if manifest.non_production_entries else {}
    validation = tuple(_audit_validation_entry(entry, evidence_root) for entry in manifest.validation_entries)
    non_production = tuple(
        NonProductionEvidenceAudit(
            contract_id=entry.contract_id,
            case_id=entry.case_id,
            status="archived" if entry.case_id in archive_reasons else "missing_archive_declaration",
            reason=archive_reasons.get(entry.case_id),
        )
        for entry in manifest.non_production_entries
    )
    return ManifestEvidenceAudit(validation=validation, non_production=non_production)


def audit_lane_a_evidence(manifest: ContractManifest | None = None, *, root: Path | str | None = None,) -> ManifestEvidenceAudit:
    """逐三元组审计 Task 5 的来源、Prometheus 和 Line Protocol 证据。

    Lane A 不要求也不接受用伪造的 VM/CMDB 文件填满 Task 6 的制品。
    """

    manifest = manifest or load_manifest()
    evidence_root = E2E_ROOT if root is None else Path(root)
    validation = tuple(_audit_validation_entry(entry, evidence_root, evidence_loader=load_lane_a_evidence) for entry in manifest.validation_entries)
    return ManifestEvidenceAudit(validation=validation, non_production=())


def _audit_validation_entry(entry: ContractEntry, evidence_root: Path, *, evidence_loader=load_evidence,) -> ValidationEvidenceAudit:
    evidence = evidence_loader(entry.case_id, root=evidence_root)
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
    return ValidationEvidenceAudit(
        contract_id=entry.contract_id, case_id=entry.case_id, missing_files=missing_files, validation_errors=tuple(validation_errors),
    )


def _format_validation_error(error: jsonschema.ValidationError) -> str:
    path = ".".join(str(item) for item in error.absolute_path) or "<root>"
    return f"{path}: {error.message}"


def _validate_provenance_source(case_id: str, provenance: dict[str, str]) -> None:
    source_type = provenance["source_type"]
    source_kind = provenance["source_kind"]
    if source_type not in SOURCE_TYPES:
        raise EvidenceValidationError(f"{case_id}: source_type 必须是受控值: {', '.join(sorted(SOURCE_TYPES))}")
    if source_kind not in SOURCE_KINDS:
        raise EvidenceValidationError(
            f"{case_id}: source_kind 必须是受控值: {', '.join(sorted(SOURCE_KINDS))}"
        )
    compatible_kinds = {
        SOURCE_TYPE_SANITIZED_REAL_ENVIRONMENT: {"docker_real", "boundary_mock"},
        SOURCE_TYPE_OFFICIAL_CLOUD_API_DOCUMENTATION: {
            "official_sdk_mock",
            "private_api_mock",
        },
    }
    if source_kind not in compatible_kinds[source_type]:
        raise EvidenceValidationError(
            f"{case_id}: source_type={source_type} 与 source_kind={source_kind} 不兼容"
        )
    if source_type == SOURCE_TYPE_SANITIZED_REAL_ENVIRONMENT:
        invalid = [field for field in _REAL_ENVIRONMENT_NOT_APPLICABLE_FIELDS if provenance[field] != _NOT_APPLICABLE]
        if invalid:
            raise EvidenceValidationError(f"{case_id}: 真实环境脱敏样本字段必须为 not_applicable: {', '.join(invalid)}")
        return

    invalid = [field for field in _REAL_ENVIRONMENT_NOT_APPLICABLE_FIELDS if provenance[field] == _NOT_APPLICABLE]
    if invalid:
        raise EvidenceValidationError(f"{case_id}: 官方云 API 文档字段不得为 not_applicable: {', '.join(invalid)}")
    vendor = provenance["vendor"].lower()
    allowed_hosts = _OFFICIAL_DOCUMENTATION_HOSTS.get(vendor)
    if allowed_hosts is None:
        raise EvidenceValidationError(f"{case_id}: vendor 不在官方文档 allowlist: {vendor}")
    if not _is_allowed_documentation_url(provenance["documentation_url"], allowed_hosts):
        raise EvidenceValidationError(f"{case_id}: documentation_url 必须是 vendor 官方 HTTPS 文档域名且不得含 userinfo/异常端口")


def _is_allowed_documentation_url(url: str, allowed_hosts: frozenset[str]) -> bool:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == "https" and parsed.hostname in allowed_hosts and parsed.username is None and parsed.password is None and port in (None, 443)
    )


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
            if _is_sensitive_key(str(key)) and not _is_redacted(child):
                findings.append(f"{filename}:{'.'.join(child_path)}")
            if _normalize_key(str(key)) in _HOST_KEYS and _is_unredacted_hostname(child):
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
    for match in _ASSIGNMENT.finditer(content):
        if _is_sensitive_key(match.group("key")) and not _is_redacted(match.group("value")):
            yield f"{filename}:{match.group(0)}"
    for match in _HOST_ASSIGNMENT.finditer(content):
        if _is_unredacted_hostname(match.group(1)):
            yield f"{filename}:{match.group(0)}"


def _is_redacted(value: Any) -> bool:
    return isinstance(value, str) and value.strip().lower() in _REDACTED_VALUES


def _normalize_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", key.lower())


def _is_sensitive_key(key: str) -> bool:
    normalized = _normalize_key(key)
    return any(marker in normalized for marker in _SENSITIVE_KEY_MARKERS)


def _is_unredacted_hostname(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip() or _is_redacted(value):
        return False
    lowered = value.lower()
    try:
        address = ipaddress.ip_address(lowered)
    except ValueError:
        address = None
    if address is not None:
        documentation_networks = (
            ipaddress.ip_network("192.0.2.0/24"),
            ipaddress.ip_network("198.51.100.0/24"),
            ipaddress.ip_network("203.0.113.0/24"),
        )
        return not (address.is_loopback or any(address in network for network in documentation_networks))
    return not any(reserved in lowered for reserved in ("example.invalid", "example.com", "example.net", "example.org"))
