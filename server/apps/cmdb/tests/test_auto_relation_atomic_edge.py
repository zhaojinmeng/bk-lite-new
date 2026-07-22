"""自动关系边在 FalkorDB/Neo4j 上的单查询原子 ensure 合同。"""

from unittest.mock import Mock

from apps.cmdb.graph.falkordb import FalkorDBClient
from apps.cmdb.graph.neo4j import Neo4jClient

EDGE_PROPERTIES = {
    "model_asst_id": "host_to_ip",
    "src_inst_id": 1,
    "dst_inst_id": 2,
    "src_model_id": "host",
    "dst_model_id": "ip",
    "asst_id": "connect",
}


def _assert_endpoint_binding(query, params):
    assert query.count("MATCH") == 2
    assert "WHERE ID(a) = $a_id" in query
    assert "WHERE ID(b) = $b_id" in query
    assert params["a_id"] == 1
    assert params["b_id"] == 2


def test_falkordb_auto_relation_ensure_is_one_merge_query(monkeypatch):
    result = object()
    client = FalkorDBClient.__new__(FalkorDBClient)
    client.ENABLE_PARAMETERIZATION = True
    client._execute_query = Mock(return_value=result)
    client.edge_to_dict = Mock(return_value={"_id": 9})
    monkeypatch.setattr(
        "apps.cmdb.graph.falkordb.FormatDBResult.get_statistics", lambda value: {"relationships_created": 1},
    )

    edge, created = client.ensure_auto_relation_edge("instance_association", 1, "instance", 2, "instance", EDGE_PROPERTIES,)

    query = client._execute_query.call_args.args[0]
    params = client._execute_query.call_args.kwargs["params"]
    assert edge == {"_id": 9}
    assert created is True
    assert client._execute_query.call_count == 1
    assert "MERGE (a)-[e:instance_association" in query
    assert "CREATE (a)-[e" not in query
    assert params["check_val"] == "host_to_ip"
    assert params["props"]["src_model_id"] == "host"
    assert params["props"]["dst_model_id"] == "ip"
    assert params["props"]["asst_id"] == "connect"
    _assert_endpoint_binding(query, params)


def test_neo4j_auto_relation_ensure_is_one_merge_query():
    relation = type("Relation", (), {"id": 9, "type": "instance_association", "_properties": {"model_asst_id": "host_to_ip"}})()

    class Result:
        def single(self):
            return {"e": relation}

        def consume(self):
            counters = type("Counters", (), {"relationships_created": 1})()
            return type("Summary", (), {"counters": counters})()

    session = Mock()
    session.run.return_value = Result()
    client = Neo4jClient.__new__(Neo4jClient)
    client.session = session

    edge, created = client.ensure_auto_relation_edge("instance_association", 1, "instance", 2, "instance", EDGE_PROPERTIES,)

    query = session.run.call_args.args[0]
    params = session.run.call_args.kwargs
    assert edge["_id"] == 9
    assert created is True
    assert session.run.call_count == 1
    assert "MERGE (a)-[e:instance_association" in query
    assert "CREATE (a)-[e" not in query
    assert params["check_val"] == "host_to_ip"
    assert params["props"]["src_model_id"] == "host"
    assert params["props"]["dst_model_id"] == "ip"
    assert params["props"]["asst_id"] == "connect"
    _assert_endpoint_binding(query, params)
