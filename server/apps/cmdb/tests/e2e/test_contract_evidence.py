import json

import pytest

from apps.cmdb.tests.e2e.contract_loader import REQUIRED, Evidence, EvidenceValidationError, audit_manifest_evidence, load_evidence
from apps.cmdb.tests.e2e.contract_manifest import ContractEntry, ContractManifest, load_manifest


def test_证据包缺失项一次性汇总(tmp_path):
    evidence = load_evidence("broken_case", root=tmp_path)

    assert evidence.missing_files == [
        "00_provenance.json",
        "01_source_raw.json",
        "02_prometheus.txt",
        "03_line_protocol.txt",
        "04_vm_response.json",
        "05_expected_cmdb.json",
        "source.schema.json",
        "vm.schema.json",
        "cmdb.schema.json",
    ]
    with pytest.raises(EvidenceValidationError, match="broken_case") as error:
        evidence.validate_complete()
    assert all(name in str(error.value) for name in evidence.missing_files)


def test_from_paths_按brief接口从目录名推导case_id(tmp_path):
    fixture_dir = tmp_path / "fixtures" / "factory_case"
    schema_dir = tmp_path / "schemas" / "factory_case"

    evidence = Evidence.from_paths(fixture_dir, schema_dir, required=REQUIRED)

    assert evidence.case_id == "factory_case"


def test_完整证据包通过_schema_溯源和敏感信息校验(complete_evidence):
    evidence = load_evidence(complete_evidence.case_id, root=complete_evidence.root)

    evidence.validate_complete()
    evidence.validate_schemas()
    evidence.validate_provenance()
    evidence.assert_no_secrets()


def test_证据包保留关键边界值(complete_evidence):
    evidence = load_evidence(complete_evidence.case_id, root=complete_evidence.root)
    source = evidence.read_json("01_source_raw.json")

    assert source["zero"] == 0
    assert source["disabled"] is False
    assert source["empty"] == ""
    assert source["unicode"] == "采集节点一"
    assert source["quote"] == '值包含"引号"'
    assert source["backslash"] == "C:\\采集\\bin"
    assert source["multiline"] == "第一行\n第二行"


def test_schema_校验错误包含_case和制品名(complete_evidence):
    source_path = complete_evidence.root / "fixtures" / complete_evidence.case_id / "01_source_raw.json"
    source_path.write_text(json.dumps({"zero": "不是整数"}), encoding="utf-8")
    evidence = load_evidence(complete_evidence.case_id, root=complete_evidence.root)

    with pytest.raises(EvidenceValidationError, match=r"complete_case.*01_source_raw.json"):
        evidence.validate_schemas()


def test_provenance_缺键明确失败(complete_evidence):
    provenance_path = complete_evidence.root / "fixtures" / complete_evidence.case_id / "00_provenance.json"
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    provenance.pop("sanitization")
    provenance_path.write_text(json.dumps(provenance), encoding="utf-8")
    evidence = load_evidence(complete_evidence.case_id, root=complete_evidence.root)

    with pytest.raises(EvidenceValidationError, match="sanitization"):
        evidence.validate_provenance()


@pytest.mark.parametrize(
    ("relative_path", "content", "message"),
    [
        ("fixtures/complete_case/01_source_raw.json", {"password": "clear-text-password"}, "password",),
        ("fixtures/complete_case/02_prometheus.txt", 'sample_info{authorization="Bearer abcdef123456"} 1\n', "Bearer",),
        ("fixtures/complete_case/03_line_protocol.txt", "sample,host=prod-db-01 value=1i\n", "prod-db-01",),
        ("fixtures/complete_case/05_expected_cmdb.json", {"host_name": "database-01.private.internal"}, "private.internal",),
        ("fixtures/complete_case/05_expected_cmdb.json", {"inst_name": "prod-db-01"}, "prod-db-01",),
    ],
)
def test_敏感信息门禁拒绝高风险键和值(complete_evidence, relative_path, content, message):
    path = complete_evidence.root / relative_path
    if isinstance(content, dict):
        path.write_text(json.dumps(content), encoding="utf-8")
    else:
        path.write_text(content, encoding="utf-8")
    evidence = load_evidence(complete_evidence.case_id, root=complete_evidence.root)

    with pytest.raises(EvidenceValidationError, match=message):
        evidence.assert_no_secrets()


def test_生产缺口与非生产归档状态由结构化_audit_返回():
    manifest = load_manifest()

    audit = audit_manifest_evidence(manifest)

    assert {item.contract_id for item in audit.production} == set(manifest.production_contracts)
    assert audit.incomplete_production
    assert all(item.missing_files for item in audit.incomplete_production)
    assert {item.contract_id for item in audit.non_production} == set(manifest.non_production_contracts)
    assert all(item.status == "archived" for item in audit.non_production)


def test_audit_对文件齐全但门禁失败的生产包返回invalid(complete_evidence):
    source_path = complete_evidence.root / "fixtures" / complete_evidence.case_id / "01_source_raw.json"
    source_path.write_text(json.dumps({"zero": "不是整数"}), encoding="utf-8")
    manifest = ContractManifest(
        production_entries=(
            ContractEntry(
                task_type="cloud",
                supported_model_id="contract_example",
                emitted_model_id="contract_example",
                case_id=complete_evidence.case_id,
                lane_a=True,
                lane_b=True,
            ),
        ),
        non_production_entries=(),
    )

    audit = audit_manifest_evidence(manifest, root=complete_evidence.root)

    assert audit.production[0].status == "invalid_evidence"
    assert "01_source_raw.json" in audit.production[0].validation_errors[0]


def test_qcloud_cvm_只迁移可独立证明的制品():
    evidence = load_evidence("qcloud_cvm")

    assert evidence.missing_files == [
        "00_provenance.json",
        "02_prometheus.txt",
        "03_line_protocol.txt",
    ]
    legacy_sources = {
        "01_source_raw.json": "01_stargazer_raw.json",
        "04_vm_response.json": "03_vm_metrics_response.json",
        "05_expected_cmdb.json": "04_expected_cmdb_result.json",
    }
    for migrated_name, legacy_name in legacy_sources.items():
        legacy = json.loads((evidence.fixture_dir / legacy_name).read_text(encoding="utf-8"))
        assert evidence.read_json(migrated_name) == legacy
