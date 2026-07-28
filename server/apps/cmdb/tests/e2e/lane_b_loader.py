import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import jsonschema

from apps.cmdb.tests.e2e.contract_manifest import ContractEntry, load_manifest


E2E_ROOT = Path(__file__).parent
LANE_B_FILES = ("04_vm_response.json", "05_expected_cmdb.json")
LANE_B_SCHEMAS = ("vm.schema.json", "cmdb.schema.json")


class LaneBValidationError(ValueError):
    """VM→CMDB 静态证据不满足 Lane B 契约。"""


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
