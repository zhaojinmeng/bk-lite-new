"""只替换 FalkorDB 最终 transport 的确定性内存图库。"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any
from unittest.mock import patch

from apps.cmdb.constants.constants import INSTANCE, MODEL


@dataclass
class _Node:
    id: int
    labels: set[str]
    properties: dict[str, Any]


@dataclass
class _Edge:
    id: int
    properties: dict[str, Any]


@dataclass
class _Result:
    header: list[tuple[int, str]]
    result_set: list[list[Any]]


class _MemoryGraphTransport:
    """实现 Task 7 会触发的有限 Cypher transport，不替换驱动业务方法。"""

    _LABEL = re.compile(r"MATCH \(n(?::(?P<label>[A-Za-z_][A-Za-z0-9_]*))?\)")
    _EQ = re.compile(r"n\.([A-Za-z_][A-Za-z0-9_]*) = \$([A-Za-z_][A-Za-z0-9_]*)")
    _IN = re.compile(r"n\.([A-Za-z_][A-Za-z0-9_]*) IN \$([A-Za-z_][A-Za-z0-9_]*)")

    def __init__(self) -> None:
        self.nodes: dict[int, _Node] = {}
        self.edges: dict[int, tuple[int, int, _Edge]] = {}
        self.queries: list[dict[str, Any]] = []
        self.creates: list[int] = []
        self.updates: list[int] = []
        self.deletes: list[int] = []
        self.edge_creates: list[int] = []
        self._next_id = 1_000_000

    def _new_id(self) -> int:
        entity_id = self._next_id
        self._next_id += 1
        return entity_id

    def seed_node(self, label: str, properties: dict[str, Any]) -> _Node:
        node = _Node(self._new_id(), {label}, dict(properties))
        self.nodes[node.id] = node
        return node

    @staticmethod
    def _result(column: str, values: list[Any]) -> _Result:
        return _Result(header=[(1, column)], result_set=[[value] for value in values])

    def _matched_nodes(self, query: str, params: dict[str, Any]) -> list[_Node]:
        label_match = self._LABEL.search(query)
        label = label_match.group("label") if label_match else None
        candidates = [node for node in self.nodes.values() if not label or label in node.labels]
        for field, parameter in self._EQ.findall(query):
            candidates = [node for node in candidates if node.properties.get(field) == params[parameter]]
        for field, parameter in self._IN.findall(query):
            candidates = [node for node in candidates if node.properties.get(field) in params[parameter]]
        return sorted(candidates, key=lambda node: node.id)

    def query(self, query: str, params: dict[str, Any] | None = None) -> _Result:
        params = dict(params or {})
        self.queries.append({"query": query, "params": params})

        if query.startswith("CREATE (n:"):
            label = query.split("CREATE (n:", 1)[1].split(")", 1)[0]
            node = self.seed_node(label, params["props"])
            self.creates.append(node.id)
            return self._result("n", [node])

        if "RETURN COUNT(e) AS count" in query:
            count = sum(
                1
                for src_id, dst_id, edge in self.edges.values()
                if {src_id, dst_id} == {params["a_id"], params["b_id"]} and edge.properties.get("model_asst_id") == params["check_val"]
            )
            return self._result("count", [count])

        if "CREATE (a)-[e:" in query:
            relation = query.split("CREATE (a)-[e:", 1)[1].split("]", 1)[0]
            edge = _Edge(self._new_id(), dict(params["props"]))
            self.edges[edge.id] = (params["a_id"], params["b_id"], edge)
            self.edge_creates.append(edge.id)
            return self._result(relation, [edge])

        if " DETACH DELETE n" in query:
            node_id = params["id"]
            self.nodes.pop(node_id, None)
            self.edges = {edge_id: item for edge_id, item in self.edges.items() if node_id not in item[:2]}
            self.deletes.append(node_id)
            return self._result("n", [])

        if " SET " in query and "WHERE ID(n) IN $ids" in query:
            updated = []
            assignments = re.findall(r"n\.([A-Za-z_][A-Za-z0-9_]*) = \$([A-Za-z_][A-Za-z0-9_]*)", query.split(" SET ", 1)[1],)
            for node_id in params["ids"]:
                node = self.nodes[node_id]
                for field, parameter in assignments:
                    node.properties[field] = params[parameter]
                updated.append(node)
                self.updates.append(node_id)
            return self._result("n", updated)

        if query.startswith("MATCH (n") and " RETURN n" in query:
            return self._result("n", self._matched_nodes(query, params))

        raise AssertionError(f"Task 7 内存 transport 未声明的 Cypher: {query}")


class GraphIntentSpy:
    """运行真实 GraphClient/FalkorDBClient，仅替换最终 graph.query transport。"""

    VOLATILE_FIELDS = {"_id", "collect_time"}

    def __init__(self) -> None:
        self.transport = _MemoryGraphTransport()
        self._patcher = None
        self._environment = None

    def seed_model(self, model_id: str, attrs: list[dict[str, Any]]) -> None:
        import json

        self.transport.seed_node(
            MODEL, {"model_id": model_id, "attrs": json.dumps(attrs, ensure_ascii=False), "unique_rules": "[]",},
        )

    def seed_instance(self, model_id: str, inst_name: str, **properties: Any) -> dict[str, Any]:
        node = self.transport.seed_node(INSTANCE, {"model_id": model_id, "inst_name": inst_name, **properties},)
        return {**node.properties, "_id": node.id}

    def __enter__(self) -> GraphIntentSpy:
        fake_client = object()
        self._environment = patch.dict(os.environ, {"FALKORDB_HOST": "127.0.0.1"})
        self._environment.start()
        self._patcher = patch("apps.cmdb.graph.falkordb.FalkorDBConnectionPool.get_connection", return_value=(fake_client, self.transport),)
        self._patcher.start()
        return self

    def __exit__(self, *args: Any, **kwargs: Any) -> None:
        if self._patcher is not None:
            self._patcher.stop()
            self._patcher = None
        if self._environment is not None:
            self._environment.stop()
            self._environment = None

    def instance_rows(self) -> list[dict[str, Any]]:
        return [{**node.properties, "_id": node.id} for node in self.transport.nodes.values() if INSTANCE in node.labels]

    def created_instances(self) -> list[dict[str, Any]]:
        return [
            {key: value for key, value in self.transport.nodes[node_id].properties.items() if key not in self.VOLATILE_FIELDS}
            for node_id in self.transport.creates
            if node_id in self.transport.nodes and INSTANCE in self.transport.nodes[node_id].labels
        ]

    def created_edges(self) -> list[dict[str, Any]]:
        result = []
        for edge_id in self.transport.edge_creates:
            src_id, dst_id, edge = self.transport.edges[edge_id]
            src = self.transport.nodes[src_id]
            dst = self.transport.nodes[dst_id]
            result.append(
                {
                    "src_model_id": edge.properties["src_model_id"],
                    "src_inst_name": src.properties.get("inst_name"),
                    "dst_model_id": edge.properties["dst_model_id"],
                    "dst_inst_name": dst.properties.get("inst_name"),
                    "asst_id": edge.properties["asst_id"],
                    "model_asst_id": edge.properties["model_asst_id"],
                }
            )
        return result
