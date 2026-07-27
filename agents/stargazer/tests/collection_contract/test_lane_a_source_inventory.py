import json
from pathlib import Path

from conftest import CONTRACT_MANIFEST_PATH, STARGAZER_ROOT

INVENTORY_PATH = Path(__file__).with_name("lane_a_source_inventory.json")
CAPTURE_ROOT = STARGAZER_ROOT / "tests" / "fixtures" / "collect"


def test_已批准迁移的非云来源均有捕获元数据和提交依据():
    inventory = json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))
    manifest = json.loads(CONTRACT_MANIFEST_PATH.read_text(encoding="utf-8"))
    validation = {
        entry["case_id"]: entry
        for entry in manifest["validation_contracts"]
        if entry["task_type"] != "cloud"
    }

    assert set(inventory) < set(validation)
    assert len(inventory) == 23
    for case_id, source in inventory.items():
        capture = json.loads(
            (CAPTURE_ROOT / source["fixture"]).read_text(encoding="utf-8")
        )
        assert capture["captured_at"].endswith("Z"), case_id
        assert capture["image"], case_id
        assert capture["container_meta"]["image"], case_id
        assert capture["raw_stdout"], case_id
        assert len(source["source_commit"]) == 9, case_id
        if case_id == "es":
            assert capture["model_id"] == "elasticsearch"
            assert source["source_model_alias"] == "elasticsearch->es"


def test_缺真实来源的非云case保持显式阻塞():
    inventory = json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))
    manifest = json.loads(CONTRACT_MANIFEST_PATH.read_text(encoding="utf-8"))
    non_cloud_cases = {
        entry["case_id"]
        for entry in manifest["validation_contracts"]
        if entry["task_type"] != "cloud"
    }

    assert non_cloud_cases - set(inventory) == {
        "disk",
        "gpu",
        "hbase",
        "host_physcial_server",
        "iis",
        "ip",
        "keepalived",
        "memory",
        "mssql",
        "network",
        "nic",
        "openresty",
        "oracle",
        "physcial_server",
        "rocketmq",
        "spark",
        "vmware_vc",
    }
