import json

import pytest

from apps.cmdb.tests.e2e.contract_loader import REQUIRED, Evidence, EvidenceValidationError, audit_manifest_evidence, load_evidence
from apps.cmdb.tests.e2e.contract_manifest import ContractEntry, ContractManifest, load_manifest


def _update_provenance(complete_evidence, **changes):
    provenance_path = complete_evidence.root / "fixtures" / complete_evidence.case_id / "00_provenance.json"
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    provenance.update(changes)
    provenance_path.write_text(json.dumps(provenance), encoding="utf-8")


def _single_production_manifest(case_id):
    return ContractManifest(
        validation_entries=(
            ContractEntry(
                task_type="cloud",
                supported_model_id="contract_example",
                emitted_model_id="contract_example",
                case_id=case_id,
                lane_a=True,
                lane_b=True,
            ),
        ),
        production_exemptions=(),
        non_production_entries=(),
    )


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
    ("vendor", "documentation_url"),
    [
        ("qcloud", "https://cloud.tencent.com/document/api/213/15753"),
        ("tencentcloud", "https://www.tencentcloud.com/document/product/213"),
        ("aliyun", "https://help.aliyun.com/document_detail/25506.html"),
        ("hwcloud", "https://support.huaweicloud.com/api-ecs/ecs_02_0101.html"),
        ("huawei_cloud", "https://developer.huaweicloud.com/intl/en-us/api-ecs/"),
        ("fusioninsight", "https://support.huawei.com/enterprise/en/cloud-computing/fusioninsight-pid-21277731"),
        ("h3c_cas", "https://www.h3c.com/en/Support/Resource_Center/EN/Cloud_Computing/"),
        ("zstack", "https://www.zstack.io/help/product_manuals/api_reference/"),
    ],
)
def test_provenance_官方云API文档只接受显式vendor域名(complete_evidence, vendor, documentation_url):
    _update_provenance(
        complete_evidence,
        source_type="official_cloud_api_documentation",
        vendor=vendor,
        service="compute",
        api_operation="DescribeResources",
        api_or_sdk_version="v1",
        documentation_url=documentation_url,
    )

    load_evidence(complete_evidence.case_id, root=complete_evidence.root).validate_provenance()


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"source_type": "contract_example"}, "source_type"),
        ({"source_type": "sanitized_real_environment", "api_operation": "DescribeInstances"}, "api_operation"),
        (
            {
                "source_type": "official_cloud_api_documentation",
                "vendor": "qcloud",
                "service": "not_applicable",
                "api_operation": "DescribeInstances",
                "api_or_sdk_version": "v1",
                "documentation_url": "https://cloud.tencent.com/document/api/213/15753",
            },
            "service",
        ),
        (
            {
                "source_type": "official_cloud_api_documentation",
                "vendor": "unknown_cloud",
                "service": "compute",
                "api_operation": "DescribeInstances",
                "api_or_sdk_version": "v1",
                "documentation_url": "https://docs.unknown.example/api",
            },
            "vendor",
        ),
    ],
)
def test_provenance_拒绝非法来源类型与not_applicable误用(complete_evidence, changes, message):
    _update_provenance(complete_evidence, **changes)

    with pytest.raises(EvidenceValidationError, match=message):
        load_evidence(complete_evidence.case_id, root=complete_evidence.root).validate_provenance()


@pytest.mark.parametrize(
    "documentation_url",
    [
        "http://cloud.tencent.com/document/api/213/15753",
        "https://cloud.tencent.com.attacker.example/document/api/213/15753",
        "https://cloud.tencent.com@attacker.example/document/api/213/15753",
        "https://attacker.example@cloud.tencent.com/document/api/213/15753",
        "https://cloud.tencent.com:444/document/api/213/15753",
    ],
)
def test_provenance_拒绝伪造域名_userinfo_非https和异常端口(complete_evidence, documentation_url):
    _update_provenance(
        complete_evidence,
        source_type="official_cloud_api_documentation",
        vendor="qcloud",
        service="compute",
        api_operation="DescribeInstances",
        api_or_sdk_version="v1",
        documentation_url=documentation_url,
    )

    with pytest.raises(EvidenceValidationError, match="documentation_url"):
        load_evidence(complete_evidence.case_id, root=complete_evidence.root).validate_provenance()


def test_audit_伪造官方文档域名不能进入ready(complete_evidence):
    _update_provenance(
        complete_evidence,
        source_type="official_cloud_api_documentation",
        vendor="qcloud",
        service="compute",
        api_operation="DescribeInstances",
        api_or_sdk_version="v1",
        documentation_url="https://cloud.tencent.com.attacker.example/api",
    )

    audit = audit_manifest_evidence(_single_production_manifest(complete_evidence.case_id), root=complete_evidence.root)

    assert audit.validation[0].status == "invalid_evidence"
    assert "documentation_url" in audit.validation[0].validation_errors[0]


@pytest.mark.parametrize(
    ("relative_path", "content", "message"),
    [
        ("fixtures/complete_case/01_source_raw.json", {"password": "clear-text-password"}, "password",),
        ("fixtures/complete_case/01_source_raw.json", {"api-key": "secret"}, "api-key",),
        ("fixtures/complete_case/02_prometheus.txt", 'sample_info{authorization="Bearer abcdef123456"} 1\n', "Bearer",),
        ("fixtures/complete_case/03_line_protocol.txt", "sample,host=prod-db-01 value=1i\n", "prod-db-01",),
        ("fixtures/complete_case/03_line_protocol.txt", "sample inst_name=prod-db-01\n", "prod-db-01",),
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


def test_敏感键多种拼写在明确脱敏后允许通过(complete_evidence):
    source_path = complete_evidence.root / "fixtures" / complete_evidence.case_id / "01_source_raw.json"
    source_path.write_text(json.dumps({"API-KEY": "***", "Access_Token": "REDACTED"}), encoding="utf-8")
    line_protocol_path = complete_evidence.root / "fixtures" / complete_evidence.case_id / "03_line_protocol.txt"
    line_protocol_path.write_text("sample,inst_name=node-01.example.invalid api-key=REDACTED\n", encoding="utf-8")

    load_evidence(complete_evidence.case_id, root=complete_evidence.root).assert_no_secrets()


def test_生产缺口与非生产归档状态由结构化_audit_返回():
    manifest = load_manifest()

    audit = audit_manifest_evidence(manifest)

    assert {item.contract_id for item in audit.validation} == set(manifest.validation_contracts)
    assert audit.incomplete_validation
    assert all(item.missing_files for item in audit.incomplete_validation)
    assert {item.contract_id for item in audit.non_production} == set(manifest.non_production_contracts)
    assert all(item.status == "archived" for item in audit.non_production)


def test_audit_对文件齐全但门禁失败的生产包返回invalid(complete_evidence):
    source_path = complete_evidence.root / "fixtures" / complete_evidence.case_id / "01_source_raw.json"
    source_path.write_text(json.dumps({"zero": "不是整数"}), encoding="utf-8")
    manifest = _single_production_manifest(complete_evidence.case_id)

    audit = audit_manifest_evidence(manifest, root=complete_evidence.root)

    assert audit.validation[0].status == "invalid_evidence"
    assert "01_source_raw.json" in audit.validation[0].validation_errors[0]


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
