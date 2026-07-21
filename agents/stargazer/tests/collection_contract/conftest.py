import sys
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

STARGAZER_ROOT = Path(__file__).resolve().parents[2]
if str(STARGAZER_ROOT) not in sys.path:
    sys.path.insert(0, str(STARGAZER_ROOT))

FIXED_TIMESTAMP_MS = 1_700_000_000_123


@dataclass(frozen=True)
class LaneAEvidence:
    source_raw: dict[str, Any]
    prometheus_text: str
    line_protocol_text: str
    expected_record_count: int


@dataclass(frozen=True)
class RepresentativeLaneACase:
    case_id: str
    model_id: str
    host: str
    publish_params: dict[str, Any]

    def run_real_adapter(self, source_raw: dict[str, Any]) -> dict[str, Any]:
        from service.collection_service import CollectionService

        service = CollectionService(
            {
                "plugin_name": f"{self.model_id}_info",
                "model_id": self.model_id,
                "host": self.host,
            }
        )
        return service._process_result(deepcopy(source_raw))


def representative_lane_a_cases():
    host_params = {
        "monitor_type": "host",
        "plugin_name": "host_info",
        "model_id": "host",
        "tags": {
            "agent_id": "agent-contract",
            "instance_id": "cmdb_1001",
            "instance_type": "host",
            "collect_type": "discovery",
            "config_type": "job",
        },
    }
    host_case = RepresentativeLaneACase(
        case_id="host-real-normalizer-single-row",
        model_id="host",
        host="192.0.2.10",
        publish_params=host_params,
    )
    host_evidence = LaneAEvidence(
        source_raw={
            "success": True,
            "result": {
                "host": [
                    {"inst_name": "node-01", "cpu_num": 4, "serial": 'SN "A", rack'}
                ]
            },
        },
        prometheus_text=(
            "# HELP host_info independently recorded host evidence\n"
            "# TYPE host_info gauge\n"
            'host_info{serial="SN \\"A\\", rack",inst_name="node-01",'
            'host="192.0.2.10",cpu_num="4",collect_status="success",'
            'bk_obj_id="host",model_id="host"} '
            f"1 {FIXED_TIMESTAMP_MS}\n"
        ),
        line_protocol_text=(
            'host_info,serial=SN\\ \\"A\\"\\,\\ rack,inst_name=node-01,'
            "host=192.0.2.10,cpu_num=4,collect_status=success,bk_obj_id=host,"
            "model_id=host,agent_id=agent-contract,instance_id=cmdb_1001,"
            "instance_type=host,collect_type=discovery,config_type=job "
            f"gauge=1i {FIXED_TIMESTAMP_MS * 1_000_000}\n"
        ),
        expected_record_count=1,
    )

    mysql_params = {
        "monitor_type": "mysql",
        "plugin_name": "mysql_info",
        "model_id": "mysql",
        "tags": {
            "agent_id": "agent-contract",
            "instance_id": "cmdb_1002",
            "instance_type": "mysql",
            "collect_type": "discovery",
            "config_type": "job",
        },
    }
    mysql_case = RepresentativeLaneACase(
        case_id="mysql-real-normalizer-two-rows",
        model_id="mysql",
        host="192.0.2.20",
        publish_params=mysql_params,
    )
    mysql_evidence = LaneAEvidence(
        source_raw={
            "success": True,
            "result": {
                "mysql": [
                    {"inst_name": "mysql-a", "port": 3306},
                    {"inst_name": "mysql-b", "port": 3307},
                ]
            },
        },
        prometheus_text=(
            "# TYPE mysql_info gauge\n"
            'mysql_info{port="3307",model_id="mysql",inst_name="mysql-b",'
            'host="192.0.2.20",collect_status="success",bk_obj_id="mysql"} '
            f"1 {FIXED_TIMESTAMP_MS}\n"
            'mysql_info{port="3306",model_id="mysql",inst_name="mysql-a",'
            'host="192.0.2.20",collect_status="success",bk_obj_id="mysql"} '
            f"1 {FIXED_TIMESTAMP_MS}\n"
        ),
        line_protocol_text=(
            "mysql_info,port=3307,model_id=mysql,inst_name=mysql-b,host=192.0.2.20,"
            "collect_status=success,bk_obj_id=mysql,agent_id=agent-contract,"
            "instance_id=cmdb_1002,instance_type=mysql,collect_type=discovery,"
            f"config_type=job gauge=1i {FIXED_TIMESTAMP_MS * 1_000_000}\n"
            "mysql_info,port=3306,model_id=mysql,inst_name=mysql-a,host=192.0.2.20,"
            "collect_status=success,bk_obj_id=mysql,agent_id=agent-contract,"
            "instance_id=cmdb_1002,instance_type=mysql,collect_type=discovery,"
            f"config_type=job gauge=1i {FIXED_TIMESTAMP_MS * 1_000_000}\n"
        ),
        expected_record_count=2,
    )
    return ((host_case, host_evidence), (mysql_case, mysql_evidence))


@pytest.fixture(params=representative_lane_a_cases(), ids=lambda item: item[0].case_id)
def representative_lane_a_case(request):
    return request.param
