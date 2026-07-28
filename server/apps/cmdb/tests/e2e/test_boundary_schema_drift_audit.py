import json
import shutil
from pathlib import Path

import pytest

from apps.cmdb.tests.e2e.boundary_schema_drift import (
    BoundarySchemaDriftError,
    audit_boundary_schema_drift,
)


E2E_ROOT = Path(__file__).parent
REPORT_PATH = E2E_ROOT / "boundary_schema_drift_report.json"


def _copy_case(tmp_path, case_id="mssql"):
    root = tmp_path / "e2e"
    fixture_dir = root / "fixtures" / case_id
    schema_dir = root / "schemas" / case_id
    fixture_dir.mkdir(parents=True)
    schema_dir.mkdir(parents=True)
    shutil.copy(
        E2E_ROOT / "fixtures" / case_id / "01_source_raw.json",
        fixture_dir / "01_source_raw.json",
    )
    shutil.copy(
        E2E_ROOT / "schemas" / case_id / "source.schema.json",
        schema_dir / "source.schema.json",
    )
    return root


def test_提交的drift报告由静态证据确定性重建():
    expected = json.loads(REPORT_PATH.read_text(encoding="utf-8"))

    assert audit_boundary_schema_drift() == expected
    assert "generated_at" not in expected
    assert expected["generated_from"]["evidence_version"] == 1
    assert [item["case_id"] for item in expected["cases"]] == sorted(
        item["case_id"] for item in expected["cases"]
    )


def test_未知可选标量字段进入非阻断报告(tmp_path):
    root = _copy_case(tmp_path)
    source_path = root / "fixtures" / "mssql" / "01_source_raw.json"
    source = json.loads(source_path.read_text(encoding="utf-8"))
    source["result"]["mssql"][0]["vendor_new_field"] = "new"
    source_path.write_text(json.dumps(source), encoding="utf-8")

    report = audit_boundary_schema_drift(root=root, case_ids={"mssql"})

    assert report["cases"] == [
        {
            "case_id": "mssql",
            "unknown_optional_fields": ["vendor_new_field"],
        }
    ]


@pytest.mark.parametrize("failure", ["unknown_nested", "known_type", "required_unknown"])
def test_嵌套漂移_已知类型错误和未声明必填字段保持阻断(tmp_path, failure):
    root = _copy_case(tmp_path)
    source_path = root / "fixtures" / "mssql" / "01_source_raw.json"
    schema_path = root / "schemas" / "mssql" / "source.schema.json"
    source = json.loads(source_path.read_text(encoding="utf-8"))
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    if failure == "unknown_nested":
        source["result"]["mssql"][0]["vendor_new_nested"] = {"unexpected": True}
    elif failure == "known_type":
        source["result"]["mssql"][0]["max_conn"] = "not-an-integer"
    else:
        schema["properties"]["result"]["properties"]["mssql"]["items"][
            "required"
        ].append("undeclared_field")
    source_path.write_text(json.dumps(source), encoding="utf-8")
    schema_path.write_text(json.dumps(schema), encoding="utf-8")

    with pytest.raises(BoundarySchemaDriftError):
        audit_boundary_schema_drift(root=root, case_ids={"mssql"})
