import json
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Union

ResourceIdentifier = Union[int, str]

CLEANUP_PRIORITY = (
    "edge",
    "instance",
    "review",
    "pending",
    "batch",
    "credential",
    "task",
    "association",
    "model",
)
NAMED_RESOURCE_KINDS = frozenset({"task", "association", "model"})


@dataclass(frozen=True)
class ResourceRef:
    kind: str
    identifier: ResourceIdentifier


@dataclass
class ValidationLedger:
    run_id: str
    _resources: list[ResourceRef] = field(default_factory=list)

    @classmethod
    def create(cls, now: str | None = None, nonce: str | None = None) -> "ValidationLedger":
        timestamp = now or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        unique_nonce = nonce or secrets.token_hex(3)
        return cls(run_id=f"crval_{timestamp}_{unique_nonce}")

    @property
    def resources(self) -> tuple[ResourceRef, ...]:
        return tuple(self._resources)

    def record(self, kind: str, identifier: ResourceIdentifier) -> None:
        if kind not in CLEANUP_PRIORITY:
            raise ValueError(f"未知资源类型: {kind}")
        if kind in NAMED_RESOURCE_KINDS and self.run_id not in str(identifier):
            raise ValueError(f"资源不属于当前 run_id: {self.run_id}")

        resource = ResourceRef(kind, identifier)
        if resource not in self._resources:
            self._resources.append(resource)

    def cleanup_plan(self) -> list[ResourceRef]:
        priority = {kind: index for index, kind in enumerate(CLEANUP_PRIORITY)}
        indexed_resources = enumerate(self._resources)
        ordered_resources = sorted(indexed_resources, key=lambda item: (priority[item[1].kind], -item[0]),)
        return [resource for _, resource in ordered_resources]

    def to_json(self) -> str:
        payload = {
            "run_id": self.run_id,
            "resources": [{"kind": resource.kind, "identifier": resource.identifier} for resource in self._resources],
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True,)

    @classmethod
    def from_json(cls, serialized: str) -> "ValidationLedger":
        data = json.loads(serialized)
        ledger = cls(run_id=data["run_id"])
        for resource in data["resources"]:
            ledger.record(resource["kind"], resource["identifier"])
        return ledger
