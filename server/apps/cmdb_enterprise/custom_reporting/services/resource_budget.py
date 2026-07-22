"""自定义上报资源预算。

集中约束 HTTP 入口、图查询分页和后台恢复循环，避免单次请求或单轮
Reconciler 因无界 payload / scope scan 占满 Web、DB 或图服务资源。
"""

from dataclasses import dataclass, replace
from time import monotonic

from apps.core.exceptions.base_app_exception import BaseAppException


@dataclass(frozen=True)
class ResourceBudget:
    max_body_bytes: int = 1024 * 1024
    max_instances: int = 1000
    max_relations: int = 1000
    max_fields_per_instance: int = 128
    graph_page_size: int = 500
    max_batch_size: int = 100
    deadline_at: float | None = None

    def with_deadline(self, *, seconds: float):
        return replace(self, deadline_at=monotonic() + max(0.0, float(seconds)))

    def ensure_time_remaining(self):
        if self.deadline_at is not None and monotonic() >= self.deadline_at:
            raise BaseAppException("资源预算已耗尽")

    def clamp_batch_size(self, value) -> int:
        try:
            requested = int(value)
        except (TypeError, ValueError):
            requested = self.max_batch_size
        return max(1, min(requested, self.max_batch_size))

    def graph_page(self, *, after_id: int = -1) -> tuple[list[dict], dict]:
        self.ensure_time_remaining()
        filters = [{"field": "id", "type": "id>", "value": int(after_id)}]
        page = {"skip": 0, "limit": self.graph_page_size}
        return filters, page

    def validate_ingest_payload(self, payload: dict):
        self.ensure_time_remaining()
        body_size = len(str(payload).encode("utf-8")) if payload is not None else 0
        if body_size > self.max_body_bytes:
            raise BaseAppException("自定义上报请求体超过资源预算")

        instances = payload.get("instances") or []
        relations = payload.get("relations") or []
        if len(instances) > self.max_instances:
            raise BaseAppException("自定义上报实例数量超过资源预算")
        if len(relations) > self.max_relations:
            raise BaseAppException("自定义上报关系数量超过资源预算")
        for instance in instances:
            if isinstance(instance, dict) and len(instance) > self.max_fields_per_instance:
                raise BaseAppException("自定义上报实例字段数量超过资源预算")


RESOURCE_BUDGET = ResourceBudget()
