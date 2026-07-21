from apps.cmdb.collection.plugins.registry import CollectionPluginRegistry
from apps.cmdb.tests.e2e.contract_manifest import expand_plugin_contract, expand_production_contracts, load_manifest


class FakeCloudPlugin:
    supported_task_type = "cloud"
    supported_model_id = "qcloud"
    field_mappings = {
        "qcloud_cvm": {"inst_name": "InstanceName"},
        "qcloud_vpc": {"inst_name": "VpcName"},
    }


def test_生产插件三元组与显式清单双向一致():
    actual = set(expand_production_contracts(CollectionPluginRegistry.get_registry_snapshot()))
    declared = set(load_manifest().production_contracts)

    missing_in_manifest = actual - declared
    stale_in_manifest = declared - actual
    assert not missing_in_manifest and not stale_in_manifest, (
        "生产采集三元组清单不一致:\n" f"missing-in-manifest={sorted(missing_in_manifest)}\n" f"stale-in-manifest={sorted(stale_in_manifest)}"
    )


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
