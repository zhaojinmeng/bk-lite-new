"""自定义上报实例所有权范围与单端点解析。"""

from dataclasses import dataclass

from apps.cmdb.constants.constants import INSTANCE
from apps.cmdb.graph.drivers.graph_client import GraphClient
from apps.cmdb_enterprise.custom_reporting.services.resource_budget import RESOURCE_BUDGET
from apps.cmdb_enterprise.custom_reporting.services.value_objects import GraphId
from apps.core.exceptions.base_app_exception import BaseAppException
from apps.core.utils.user_group import normalize_user_group_ids


@dataclass(frozen=True)
class OwnedInstanceScope:
    model_id: str
    collect_task: str
    organizations: tuple[int, ...]

    @classmethod
    def from_task(cls, task, model_id: str) -> "OwnedInstanceScope":
        organizations = tuple(dict.fromkeys(normalize_user_group_ids(task.team)))
        if not model_id:
            raise BaseAppException("实例所有权范围缺少模型")
        if not organizations:
            raise BaseAppException("实例所有权范围缺少组织")
        return cls(
            model_id=model_id,
            collect_task=f"cr_{task.id}",
            organizations=organizations,
        )

    def filters(self) -> list[dict]:
        return [
            {"field": "model_id", "type": "str=", "value": self.model_id},
            {"field": "collect_task", "type": "str=", "value": self.collect_task},
            {
                "field": "organization",
                "type": "list[]",
                "value": list(self.organizations),
            },
        ]

    def owns(self, instance: dict) -> bool:
        organization = instance.get("organization")
        return (
            instance.get("model_id") == self.model_id
            and instance.get("collect_task") == self.collect_task
            and isinstance(organization, (list, tuple, set))
            and set(self.organizations).issubset(set(organization))
        )

    def query(
        self,
        extra_filters: list[dict] | None = None,
        graph_client_cls=None,
    ) -> list[dict]:
        client_cls = graph_client_cls or GraphClient
        base_filters = self.filters() + list(extra_filters or [])
        results = []
        after_id = -1
        with client_cls() as ag:
            while True:
                keyset_filters, page = RESOURCE_BUDGET.graph_page(after_id=after_id)
                instances, _ = ag.query_entity(
                    INSTANCE,
                    base_filters + keyset_filters,
                    page=page,
                    include_count=False,
                )
                if not instances:
                    break
                results.extend(instance for instance in instances if self.owns(instance))
                last_ids = [GraphId(instance["_id"]).value for instance in instances if instance.get("_id") is not None]
                if not last_ids or len(instances) < page["limit"]:
                    break
                after_id = max(last_ids)
        return results

    def owned_ids(self, inst_ids: list, graph_client_cls=None) -> list[int]:
        normalized_ids = list(dict.fromkeys(GraphId(inst_id).value for inst_id in inst_ids))
        if not normalized_ids:
            return []
        instances = self.query(
            [{"field": "id", "type": "id[]", "value": normalized_ids}],
            graph_client_cls=graph_client_cls,
        )
        owned_id_set = {
            GraphId(instance.get("_id")).value
            for instance in instances
            if instance.get("_id") is not None
        }
        return [inst_id for inst_id in normalized_ids if inst_id in owned_id_set]


@dataclass(frozen=True)
class OwnedInstanceRef:
    model_id: str
    instance_id: int | None = None
    identity: tuple[tuple[str, object], ...] = ()

    @classmethod
    def from_payload(cls, payload: dict) -> "OwnedInstanceRef":
        model_id = payload.get("model_id", "")
        if "_id" in payload:
            return cls(
                model_id=model_id,
                instance_id=GraphId(payload["_id"]).value,
            )
        identity = payload.get("identity")
        if not isinstance(identity, dict) or not identity:
            return cls(model_id=model_id)
        return cls(model_id=model_id, identity=tuple(identity.items()))

    def filters(self) -> list[dict]:
        if self.instance_id is not None:
            return [{"field": "id", "type": "id=", "value": self.instance_id}]

        filters = []
        for field, value in self.identity:
            if isinstance(value, bool):
                value = str(value).lower()
                field_type = "str="
            elif isinstance(value, int):
                field_type = "int="
            else:
                field_type = "str="
            filters.append({"field": field, "type": field_type, "value": value})
        return filters


def resolve_owned_instance(task, ref: OwnedInstanceRef, graph_client_cls=None) -> dict | None:
    filters = ref.filters()
    if not filters:
        return None
    scope = OwnedInstanceScope.from_task(task, ref.model_id)
    instances = scope.query(filters, graph_client_cls=graph_client_cls)
    if ref.instance_id is not None:
        instances = [
            instance
            for instance in instances
            if instance.get("_id") is not None
            and GraphId(instance["_id"]).value == ref.instance_id
        ]
    if len(instances) > 1:
        raise BaseAppException("实例查询结果不唯一")
    return instances[0] if instances else None
