import json
import re
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
INTEGER_OWNED_RESOURCE_KINDS = frozenset({"task", "credential", "batch"})
RUN_ID_PATTERN = re.compile(r"\Acrval_(?P<timestamp>[0-9]{8}T[0-9]{6}Z)_(?P<nonce>[A-Za-z0-9]{6,})\Z")


def _validate_run_id(run_id: object) -> str:
    if not isinstance(run_id, str):
        raise ValueError("run_id 格式无效")
    match = RUN_ID_PATTERN.fullmatch(run_id)
    if match is None:
        raise ValueError("run_id 格式无效")
    try:
        datetime.strptime(match.group("timestamp"), "%Y%m%dT%H%M%SZ")
    except ValueError as exc:
        raise ValueError("run_id 格式无效") from exc
    return run_id


@dataclass(frozen=True)
class ResourceRef:
    kind: str
    identifier: ResourceIdentifier


@dataclass(frozen=True)
class ValidationLedger:
    run_id: str
    _resources: list[ResourceRef] = field(default_factory=list, init=False, repr=False)

    def __post_init__(self) -> None:
        _validate_run_id(self.run_id)

    @classmethod
    def create(cls, now: str | None = None, nonce: str | None = None) -> "ValidationLedger":
        timestamp = now or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        unique_nonce = nonce or secrets.token_hex(16)
        return cls(run_id=f"crval_{timestamp}_{unique_nonce}")

    @property
    def resources(self) -> tuple[ResourceRef, ...]:
        return tuple(self._resources)

    def record(self, kind: str, identifier: ResourceIdentifier) -> None:
        if kind not in CLEANUP_PRIORITY:
            raise ValueError(f"未知资源类型: {kind}")
        if kind in NAMED_RESOURCE_KINDS and not isinstance(identifier, str):
            raise ValueError("名称型资源 identifier 必须是 str")
        if type(identifier) not in (int, str):
            raise ValueError("identifier 必须是 int 或 str")
        if kind in INTEGER_OWNED_RESOURCE_KINDS and isinstance(identifier, str):
            prefix = f"{self.run_id}:"
            raw_identifier = identifier[len(prefix) :] if identifier.startswith(prefix) else ""
            legacy_named_identifier = kind == "task" and (identifier == self.run_id or identifier.startswith(f"{self.run_id}_"))
            if not legacy_named_identifier and (not raw_identifier.isascii() or not raw_identifier.isdecimal() or raw_identifier.startswith("0")):
                raise ValueError(f"资源不属于当前 run_id: {self.run_id}")
        elif kind in NAMED_RESOURCE_KINDS:
            owned = identifier == self.run_id or identifier.startswith(f"{self.run_id}_")
            if kind in {"association", "model"}:
                owned = owned or identifier.startswith(f"{self.run_id.lower()}_")
            if not owned:
                raise ValueError(f"资源不属于当前 run_id: {self.run_id}")

        resource = ResourceRef(kind, identifier)
        if resource not in self._resources:
            self._resources.append(resource)

    def cleanup_plan(self) -> list[ResourceRef]:
        priority = {kind: index for index, kind in enumerate(CLEANUP_PRIORITY)}
        indexed_resources = enumerate(self._resources)
        ordered_resources = sorted(
            indexed_resources,
            key=lambda item: (priority[item[1].kind], -item[0]),
        )
        return [resource for _, resource in ordered_resources]

    def to_json(self) -> str:
        payload = {
            "run_id": self.run_id,
            "resources": [{"kind": resource.kind, "identifier": resource.identifier} for resource in self._resources],
        }
        return json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
        )

    @classmethod
    def from_json(cls, serialized: str) -> "ValidationLedger":
        data = json.loads(serialized)
        if not isinstance(data, dict) or "run_id" not in data or not isinstance(data.get("resources"), list):
            raise ValueError("账本 JSON 结构无效")
        ledger = cls(run_id=data["run_id"])
        for resource in data["resources"]:
            if not isinstance(resource, dict) or "kind" not in resource or "identifier" not in resource:
                raise ValueError("账本 JSON 结构无效")
            ledger.record(resource["kind"], resource["identifier"])
        return ledger
