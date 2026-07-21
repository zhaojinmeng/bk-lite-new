import copy
from pathlib import Path

import pytest

from apps.cmdb.collection.plugins.community.cloud.h3c_cas import H3CCASCollectionPlugin
from apps.cmdb.collection.plugins.community.cloud.zstack import ZStackCollectionPlugin
from apps.cmdb.collection.plugins.registry import CollectionPluginRegistry
from apps.cmdb.tests.e2e.contract_manifest import expand_contract_partition, expand_plugin_contract, load_manifest, parse_manifest


class FakeCloudPlugin:
    supported_task_type = "cloud"
    supported_model_id = "qcloud"
    field_mappings = {
        "qcloud_cvm": {"inst_name": "InstanceName"},
        "qcloud_vpc": {"inst_name": "VpcName"},
    }


def test_生产插件三元组与可测试清单加显式豁免双向一致():
    actual = expand_contract_partition(CollectionPluginRegistry.get_registry_snapshot())
    declared = load_manifest()

    declared_production = set(declared.validation_contracts) | set(declared.exempted_contracts)

    assert not actual.production_contracts - declared_production, (
        "missing-in-manifest=" f"{sorted(actual.production_contracts - declared_production)}"
    )
    assert not declared_production - actual.production_contracts, "stale-in-manifest=" f"{sorted(declared_production - actual.production_contracts)}"


def test_可测试生产_生产豁免_非生产三集合严格分区注册表全集():
    actual = expand_contract_partition(CollectionPluginRegistry.get_registry_snapshot())
    declared = load_manifest()

    declared_validation = set(declared.validation_contracts)
    declared_exemptions = set(declared.exempted_contracts)
    declared_non_production = set(declared.non_production_contracts)

    assert not declared_validation & declared_exemptions
    assert not declared_validation & declared_non_production
    assert not declared_exemptions & declared_non_production
    assert declared_validation | declared_exemptions == actual.production_contracts
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


@pytest.mark.parametrize(
    ("model_id", "plugin_cls"), [("h3c_cas", H3CCASCollectionPlugin), ("zstack", ZStackCollectionPlugin),],
)
def test_显式占位云插件不计入生产覆盖(model_id, plugin_cls):
    repository_root = Path(__file__).resolve().parents[5]
    snapshot = CollectionPluginRegistry.get_registry_snapshot()
    plugin = next(item for item in snapshot if item["model_id"] == model_id)

    assert plugin_cls.metric_names == []
    assert plugin_cls.field_mappings == {}
    assert "stub" in (plugin_cls.__doc__ or "").lower()
    assert not (repository_root / "agents" / "stargazer" / "plugins" / "inputs" / model_id / "plugin.yml").exists()
    assert plugin["is_production"] is False
    assert ("cloud", model_id, model_id) in set(load_manifest().non_production_contracts)


def test_k8s保持生产身份但以稳定来源理由显式豁免验证():
    snapshot = CollectionPluginRegistry.get_registry_snapshot()
    k8s = next(item for item in snapshot if item["model_id"] == "k8s_cluster")
    manifest = load_manifest()
    exemption = manifest.production_exemptions[0]

    assert k8s["is_production"] is True
    assert exemption.contract_id == ("k8s", "k8s_cluster", "k8s_cluster")
    assert exemption.lane_a is False
    assert exemption.lane_b is False
    assert exemption.source_kind == "external_kube_state_metrics_vm"
    assert exemption.reason == "用户批准：外部 kube-state-metrics 直接写入 VM，不经过 Stargazer"
    assert exemption.contract_id not in set(manifest.validation_contracts)
    assert exemption.contract_id not in set(manifest.non_production_contracts)


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


def _manifest(validation_contracts=None, non_production_contracts=None):
    return _three_way_manifest(validation_contracts=validation_contracts, non_production_contracts=non_production_contracts,)


def _exemption(**overrides):
    entry = _entry(
        task_type="k8s", supported_model_id="k8s_cluster", emitted_model_id="k8s_cluster", case_id="k8s_cluster", lane_a=False, lane_b=False,
    )
    entry.update(
        reason="用户批准：外部 kube-state-metrics 直接写入 VM，不经过 Stargazer", source_kind="external_kube_state_metrics_vm",
    )
    entry.update(overrides)
    return entry


def _three_way_manifest(
    validation_contracts=None, production_exemptions=None, non_production_contracts=None,
):
    return {
        "validation_contracts": (validation_contracts if validation_contracts is not None else [_entry()]),
        "production_exemptions": (production_exemptions if production_exemptions is not None else []),
        "non_production_contracts": (non_production_contracts if non_production_contracts is not None else []),
    }


def test_三集合清单解析为语义明确的可测试生产与豁免():
    manifest = parse_manifest(_three_way_manifest(production_exemptions=[_exemption()]))

    assert manifest.validation_contracts == (("cloud", "qcloud", "qcloud_cvm"),)
    assert manifest.exempted_contracts == (("k8s", "k8s_cluster", "k8s_cluster"),)


@pytest.mark.parametrize(
    ("entry", "message"),
    [
        (_exemption(reason=None), "reason 必须是字符串"),
        (_exemption(reason=""), "reason 必须是非空字符串"),
        (_exemption(source_kind=None), "source_kind 必须是字符串"),
        (_exemption(source_kind=""), "source_kind 必须是非空字符串"),
        (_exemption(lane_a=True), "lane"),
    ],
)
def test_生产豁免拒绝缺理由_缺来源和错误lane(entry, message):
    with pytest.raises(ValueError, match=message):
        parse_manifest(_three_way_manifest(production_exemptions=[entry]))


def test_三集合顶层缺键与跨集合重复均拒绝():
    missing_exemptions = _three_way_manifest()
    missing_exemptions.pop("production_exemptions")
    with pytest.raises(ValueError, match="清单字段必须精确为"):
        parse_manifest(missing_exemptions)

    duplicate = _exemption(task_type="cloud", supported_model_id="qcloud", emitted_model_id="qcloud_cvm", case_id="qcloud_cvm",)
    with pytest.raises(ValueError, match="重复"):
        parse_manifest(_three_way_manifest(production_exemptions=[duplicate]))


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
        parse_manifest(_manifest(validation_contracts=[entry]))


def test_清单条目拒绝缺少固定字段():
    entry = _entry()
    entry.pop("emitted_model_id")

    with pytest.raises(ValueError, match="字段必须精确为"):
        parse_manifest(_manifest(validation_contracts=[entry]))


@pytest.mark.parametrize(
    "manifest",
    [
        _manifest(validation_contracts=[_entry(), copy.deepcopy(_entry())]),
        _manifest(validation_contracts=[_entry(), _entry(emitted_model_id="qcloud_vpc")]),
        _manifest(non_production_contracts=[_entry(emitted_model_id="qcloud_vpc", lane_a=False, lane_b=False)]),
    ],
)
def test_清单条目拒绝重复三元组或重复_case_id(manifest):
    with pytest.raises(ValueError, match="重复"):
        parse_manifest(manifest)


@pytest.mark.parametrize(
    "manifest", [_manifest(validation_contracts=[_entry(lane_a=False)]), _manifest(non_production_contracts=[_entry(lane_a=False, lane_b=True)]),],
)
def test_清单条目强制生产与非生产_lane_规则(manifest):
    with pytest.raises(ValueError, match="lane"):
        parse_manifest(manifest)
