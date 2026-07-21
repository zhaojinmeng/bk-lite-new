import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from apps.cmdb.collection.plugins.registry import emitted_model_ids

Contract = tuple[str, str, str]
_ENTRY_FIELDS = {
    "task_type",
    "supported_model_id",
    "emitted_model_id",
    "case_id",
    "lane_a",
    "lane_b",
}
_EXEMPTION_FIELDS = _ENTRY_FIELDS | {"reason", "source_kind"}
_MANIFEST_FIELDS = {
    "validation_contracts",
    "production_exemptions",
    "non_production_contracts",
}
_MANIFEST_PATH = Path(__file__).with_suffix(".json")


@dataclass(frozen=True)
class ContractEntry:
    task_type: str
    supported_model_id: str
    emitted_model_id: str
    case_id: str
    lane_a: bool
    lane_b: bool

    @property
    def contract_id(self) -> Contract:
        return (self.task_type, self.supported_model_id, self.emitted_model_id)


@dataclass(frozen=True)
class ProductionExemptionEntry:
    task_type: str
    supported_model_id: str
    emitted_model_id: str
    case_id: str
    lane_a: bool
    lane_b: bool
    reason: str
    source_kind: str

    @property
    def contract_id(self) -> Contract:
        return (self.task_type, self.supported_model_id, self.emitted_model_id)


@dataclass(frozen=True)
class ContractManifest:
    validation_entries: tuple[ContractEntry, ...]
    production_exemptions: tuple[ProductionExemptionEntry, ...]
    non_production_entries: tuple[ContractEntry, ...]

    @property
    def validation_contracts(self) -> tuple[Contract, ...]:
        return tuple(entry.contract_id for entry in self.validation_entries)

    @property
    def exempted_contracts(self) -> tuple[Contract, ...]:
        return tuple(entry.contract_id for entry in self.production_exemptions)

    @property
    def non_production_contracts(self) -> tuple[Contract, ...]:
        return tuple(entry.contract_id for entry in self.non_production_entries)


@dataclass(frozen=True)
class ContractPartition:
    production_contracts: frozenset[Contract]
    non_production_contracts: frozenset[Contract]

    @property
    def all_contracts(self) -> frozenset[Contract]:
        return self.production_contracts | self.non_production_contracts


def _parse_entry(entry: Any, section: str) -> ContractEntry:
    if not isinstance(entry, dict) or set(entry) != _ENTRY_FIELDS:
        raise ValueError(f"{section} 条目字段必须精确为 {sorted(_ENTRY_FIELDS)}")

    for field in ("task_type", "supported_model_id", "emitted_model_id", "case_id"):
        if not isinstance(entry[field], str):
            raise ValueError(f"{field} 必须是字符串")
    if not entry["case_id"].strip():
        raise ValueError("case_id 必须是非空字符串")
    for field in ("lane_a", "lane_b"):
        if type(entry[field]) is not bool:
            raise ValueError(f"{field} 必须是 bool")

    is_validation = section == "validation_contracts"
    if entry["lane_a"] is not is_validation or entry["lane_b"] is not is_validation:
        raise ValueError(f"{section} 的 lane_a/lane_b 必须均为 {is_validation}")

    return ContractEntry(**entry)


def _parse_exemption(entry: Any) -> ProductionExemptionEntry:
    section = "production_exemptions"
    if not isinstance(entry, dict) or set(entry) != _EXEMPTION_FIELDS:
        raise ValueError(f"{section} 条目字段必须精确为 {sorted(_EXEMPTION_FIELDS)}")

    for field in (
        "task_type",
        "supported_model_id",
        "emitted_model_id",
        "case_id",
        "reason",
        "source_kind",
    ):
        if not isinstance(entry[field], str):
            raise ValueError(f"{field} 必须是字符串")
        if not entry[field].strip():
            raise ValueError(f"{field} 必须是非空字符串")
    for field in ("lane_a", "lane_b"):
        if type(entry[field]) is not bool:
            raise ValueError(f"{field} 必须是 bool")
        if entry[field] is not False:
            raise ValueError(f"{section} 的 lane_a/lane_b 必须均为 False")

    return ProductionExemptionEntry(**entry)


def _parse_entries(data: dict[str, Any], section: str) -> tuple[ContractEntry, ...]:
    entries = data[section]
    if not isinstance(entries, list):
        raise ValueError(f"{section} 必须是列表")
    return tuple(_parse_entry(entry, section) for entry in entries)


def _parse_exemptions(data: dict[str, Any]) -> tuple[ProductionExemptionEntry, ...]:
    entries = data["production_exemptions"]
    if not isinstance(entries, list):
        raise ValueError("production_exemptions 必须是列表")
    return tuple(_parse_exemption(entry) for entry in entries)


def _validate_unique(entries: tuple[ContractEntry | ProductionExemptionEntry, ...],) -> None:
    contract_ids = [entry.contract_id for entry in entries]
    if len(contract_ids) != len(set(contract_ids)):
        raise ValueError("contract_id 重复")
    case_ids = [entry.case_id for entry in entries]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("case_id 重复")


def parse_manifest(data: Any) -> ContractManifest:
    if not isinstance(data, dict) or set(data) != _MANIFEST_FIELDS:
        raise ValueError(f"清单字段必须精确为 {sorted(_MANIFEST_FIELDS)}")

    validation_entries = _parse_entries(data, "validation_contracts")
    production_exemptions = _parse_exemptions(data)
    non_production_entries = _parse_entries(data, "non_production_contracts")
    _validate_unique(validation_entries + production_exemptions + non_production_entries)
    return ContractManifest(
        validation_entries=validation_entries, production_exemptions=production_exemptions, non_production_entries=non_production_entries,
    )


def load_manifest() -> ContractManifest:
    with _MANIFEST_PATH.open(encoding="utf-8") as manifest_file:
        return parse_manifest(json.load(manifest_file))


def expand_plugin_contract(plugin_cls: type) -> set[Contract]:
    return {(plugin_cls.supported_task_type, plugin_cls.supported_model_id, emitted_model_id,) for emitted_model_id in emitted_model_ids(plugin_cls)}


def expand_contract_partition(snapshot: list[dict]) -> ContractPartition:
    production_contracts = set()
    non_production_contracts = set()
    for item in snapshot:
        contracts = {(item["task_type"], item["model_id"], emitted_model_id) for emitted_model_id in item["emitted_model_ids"]}
        target = production_contracts if item["is_production"] else non_production_contracts
        target.update(contracts)
    return ContractPartition(production_contracts=frozenset(production_contracts), non_production_contracts=frozenset(non_production_contracts),)


def expand_production_contracts(snapshot: list[dict]) -> set[Contract]:
    return set(expand_contract_partition(snapshot).production_contracts)
