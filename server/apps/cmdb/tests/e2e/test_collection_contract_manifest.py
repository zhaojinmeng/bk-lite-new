import copy

import pytest

from apps.cmdb.collection.plugins.registry import CollectionPluginRegistry
from apps.cmdb.tests.e2e.contract_manifest import expand_contract_partition, expand_plugin_contract, load_manifest, parse_manifest


class FakeCloudPlugin:
    supported_task_type = "cloud"
    supported_model_id = "qcloud"
    field_mappings = {
        "qcloud_cvm": {"inst_name": "InstanceName"},
        "qcloud_vpc": {"inst_name": "VpcName"},
    }


def test_生产插件三元组与显式清单双向一致():
    actual = expand_contract_partition(CollectionPluginRegistry.get_registry_snapshot())
    declared = load_manifest()

    assert not actual.production_contracts - set(
        declared.production_contracts
    ), f"missing-in-manifest={sorted(actual.production_contracts - set(declared.production_contracts))}"
    assert (
        not set(declared.production_contracts) - actual.production_contracts
    ), f"stale-in-manifest={sorted(set(declared.production_contracts) - actual.production_contracts)}"


def test_生产与非生产清单共同精确分区注册表全集():
    actual = expand_contract_partition(CollectionPluginRegistry.get_registry_snapshot())
    declared = load_manifest()

    declared_production = set(declared.production_contracts)
    declared_non_production = set(declared.non_production_contracts)

    assert not declared_production & declared_non_production
    assert declared_production | declared_non_production == actual.all_contracts
    assert declared_non_production == actual.non_production_contracts


def test_父插件必须展开所有产出模型():
    contracts = expand_plugin_contract(FakeCloudPlugin)

    assert contracts == {
        ("cloud", "qcloud", "qcloud_cvm"),
        ("cloud", "qcloud", "qcloud_vpc"),
    }


def test_许可证阻塞插件不计入生产覆盖():
    snapshot = CollectionPluginRegistry.get_registry_snapshot()
    tuxedo = next(item for item in snapshot if item["model_id"] == "tuxedo")

    assert tuxedo["is_production"] is False
    assert ("middleware", "tuxedo", "tuxedo") in set(load_manifest().non_production_contracts)


def _entry(**overrides):
    entry = {
        "task_type": "cloud",
        "supported_model_id": "qcloud",
        "emitted_model_id": "qcloud_cvm",
        "case_id": "qcloud_cvm",
        "lane_a": True,
        "lane_b": True,
    }
    entry.update(overrides)
    return entry


def _manifest(production_contracts=None, non_production_contracts=None):
    return {
        "production_contracts": production_contracts if production_contracts is not None else [_entry()],
        "non_production_contracts": non_production_contracts if non_production_contracts is not None else [],
    }


@pytest.mark.parametrize(
    ("entry", "message"),
    [
        (_entry(case_id=None), "case_id 必须是字符串"),
        (_entry(case_id=""), "case_id 必须是非空字符串"),
        (_entry(lane_a=1), "lane_a 必须是 bool"),
        (_entry(extra="unexpected"), "字段必须精确为"),
    ],
)
def test_清单条目拒绝缺键错类型和额外字段(entry, message):
    with pytest.raises(ValueError, match=message):
        parse_manifest(_manifest(production_contracts=[entry]))


def test_清单条目拒绝缺少固定字段():
    entry = _entry()
    entry.pop("emitted_model_id")

    with pytest.raises(ValueError, match="字段必须精确为"):
        parse_manifest(_manifest(production_contracts=[entry]))


@pytest.mark.parametrize(
    "manifest",
    [
        _manifest(production_contracts=[_entry(), copy.deepcopy(_entry())]),
        _manifest(production_contracts=[_entry(), _entry(emitted_model_id="qcloud_vpc")]),
        _manifest(non_production_contracts=[_entry(emitted_model_id="qcloud_vpc", lane_a=False, lane_b=False)]),
    ],
)
def test_清单条目拒绝重复三元组或重复_case_id(manifest):
    with pytest.raises(ValueError, match="重复"):
        parse_manifest(manifest)


@pytest.mark.parametrize(
    "manifest", [_manifest(production_contracts=[_entry(lane_a=False)]), _manifest(non_production_contracts=[_entry(lane_a=False, lane_b=True)]),],
)
def test_清单条目强制生产与非生产_lane_规则(manifest):
    with pytest.raises(ValueError, match="lane"):
        parse_manifest(manifest)
