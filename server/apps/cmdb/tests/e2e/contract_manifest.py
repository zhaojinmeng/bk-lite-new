import json
from dataclasses import dataclass
from pathlib import Path

from apps.cmdb.collection.plugins.registry import emitted_model_ids

Contract = tuple[str, str, str]
_MANIFEST_PATH = Path(__file__).with_suffix(".json")


@dataclass(frozen=True)
class ContractManifest:
    production_contracts: tuple[Contract, ...]
    non_production_contracts: tuple[Contract, ...]


def _contracts(entries: list[dict]) -> tuple[Contract, ...]:
    return tuple((entry["task_type"], entry["supported_model_id"], entry["emitted_model_id"],) for entry in entries)


def load_manifest() -> ContractManifest:
    with _MANIFEST_PATH.open(encoding="utf-8") as manifest_file:
        data = json.load(manifest_file)
    return ContractManifest(
        production_contracts=_contracts(data["production_contracts"]), non_production_contracts=_contracts(data["non_production_contracts"]),
    )


def expand_plugin_contract(plugin_cls: type) -> set[Contract]:
    return {(plugin_cls.supported_task_type, plugin_cls.supported_model_id, emitted_model_id,) for emitted_model_id in emitted_model_ids(plugin_cls)}


def expand_production_contracts(snapshot: list[dict]) -> set[Contract]:
    return {
        (item["task_type"], item["model_id"], emitted_model_id)
        for item in snapshot
        if item["is_production"]
        for emitted_model_id in item["emitted_model_ids"]
    }
