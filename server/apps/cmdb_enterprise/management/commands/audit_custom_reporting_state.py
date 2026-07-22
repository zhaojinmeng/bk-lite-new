"""审计自定义上报存量状态。

默认只输出 JSON dry-run 报告；只有显式传入 ``--apply-safe-fixes`` 时才执行
可证明安全、幂等的关系库修复。命令不访问图存储，也不删除任何图事实。
"""

import json

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.cmdb_enterprise.custom_reporting.models import (
    CustomReportingPendingRelation,
    CustomReportingTask,
    PendingRelationDelivery,
)
from apps.cmdb_enterprise.custom_reporting.services import relation_service
from apps.cmdb_enterprise.custom_reporting.services.schema_service import compile_task_schema
from apps.core.exceptions.base_app_exception import BaseAppException


class Command(BaseCommand):
    help = "审计自定义上报存量状态，默认 dry-run；--apply-safe-fixes 仅执行安全幂等修复。"

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply-safe-fixes",
            action="store_true",
            help="执行可证明安全的关系库修复；不会写图或删除无法证明归属的数据。",
        )

    def handle(self, *args, **options):
        apply_safe_fixes = bool(options.get("apply_safe_fixes"))
        findings = []
        applied = []

        self._audit_tasks(findings, applied, apply_safe_fixes=apply_safe_fixes)
        self._audit_deliveries(findings, applied, apply_safe_fixes=apply_safe_fixes)
        self._audit_legacy_pending(findings, applied, apply_safe_fixes=apply_safe_fixes)

        report = {
            "dry_run": not apply_safe_fixes,
            "summary": {
                "findings": len(findings),
                "applied": len(applied),
            },
            "findings": findings,
            "applied": applied,
        }
        self.stdout.write(json.dumps(report, ensure_ascii=False, sort_keys=True))

    def _audit_tasks(self, findings: list[dict], applied: list[dict], *, apply_safe_fixes: bool):
        for task in CustomReportingTask.objects.order_by("id").only("id", "name", "config", "sync_status"):
            try:
                compile_task_schema(task.config)
            except BaseAppException as error:
                finding = {
                    "code": "invalid_task_config",
                    "task_id": task.id,
                    "task_name": task.name,
                    "message": str(error),
                    "safe_action": "mark_task_degraded",
                }
                findings.append(finding)
                if apply_safe_fixes and task.sync_status != CustomReportingTask.SYNC_DEGRADED:
                    updated = CustomReportingTask.objects.filter(id=task.id).exclude(
                        sync_status=CustomReportingTask.SYNC_DEGRADED
                    ).update(sync_status=CustomReportingTask.SYNC_DEGRADED)
                    if updated:
                        applied.append({"code": "mark_task_degraded", "task_id": task.id})

    def _audit_deliveries(self, findings: list[dict], applied: list[dict], *, apply_safe_fixes: bool):
        deliveries = PendingRelationDelivery.objects.exclude(state=PendingRelationDelivery.STATE_DEAD_LETTER).order_by("id")
        for delivery in deliveries:
            payload = delivery.relation_payload if isinstance(delivery.relation_payload, dict) else {}
            if payload.get("mapping") not in (None, ""):
                continue
            finding = {
                "code": "relation_missing_mapping",
                "task_id": delivery.task_id,
                "delivery_id": delivery.id,
                "fingerprint": delivery.fingerprint,
                "safe_action": "manual_failed",
            }
            findings.append(finding)
            if apply_safe_fixes:
                updated = PendingRelationDelivery.objects.filter(id=delivery.id).exclude(
                    state=PendingRelationDelivery.STATE_DEAD_LETTER
                ).update(
                    state=PendingRelationDelivery.STATE_DEAD_LETTER,
                    owner_token="",
                    lease_expires_at=None,
                    last_error="关系载荷缺少 mapping，需人工处理",
                )
                if updated:
                    applied.append({"code": "manual_failed", "delivery_id": delivery.id})

    def _audit_legacy_pending(self, findings: list[dict], applied: list[dict], *, apply_safe_fixes: bool):
        for pending in CustomReportingPendingRelation.objects.select_related("task").order_by("id"):
            payload = pending.relation_payload if isinstance(pending.relation_payload, dict) else {}
            if payload.get("mapping") in (None, ""):
                findings.append(
                    {
                        "code": "relation_missing_mapping",
                        "task_id": pending.task_id,
                        "pending_relation_id": pending.id,
                        "safe_action": "manual_failed",
                    }
                )
                continue

            try:
                fingerprint = relation_service._relation_fingerprint(payload)
                payload_hash = relation_service._payload_hash(payload)
            except (TypeError, ValueError) as error:
                findings.append(
                    {
                        "code": "pending_payload_not_canonical",
                        "task_id": pending.task_id,
                        "pending_relation_id": pending.id,
                        "message": str(error),
                        "safe_action": "manual_failed",
                    }
                )
                continue

            existing = PendingRelationDelivery.objects.filter(task=pending.task, fingerprint=fingerprint).first()
            if existing is not None:
                if (
                    existing.payload_hash == payload_hash
                    and relation_service._canonical_relation_payload(existing.relation_payload)
                    == relation_service._canonical_relation_payload(payload)
                ):
                    findings.append(
                        {
                            "code": "legacy_pending_duplicate",
                            "task_id": pending.task_id,
                            "pending_relation_id": pending.id,
                            "delivery_id": existing.id,
                            "safe_action": "already_deduped",
                        }
                    )
                    continue
                findings.append(
                    {
                        "code": "pending_fingerprint_conflict",
                        "task_id": pending.task_id,
                        "pending_relation_id": pending.id,
                        "fingerprint": fingerprint,
                        "safe_action": "manual_failed",
                    }
                )
                continue

            findings.append(
                {
                    "code": "legacy_pending_without_delivery",
                    "task_id": pending.task_id,
                    "pending_relation_id": pending.id,
                    "fingerprint": fingerprint,
                    "safe_action": "create_delivery",
                }
            )
            if apply_safe_fixes:
                created = self._create_delivery_from_legacy_pending(pending, fingerprint=fingerprint, payload_hash=payload_hash)
                if created:
                    applied.append({"code": "create_delivery", "pending_relation_id": pending.id})

    def _create_delivery_from_legacy_pending(self, pending, *, fingerprint: str, payload_hash: str) -> bool:
        with transaction.atomic():
            existing = PendingRelationDelivery.objects.filter(task=pending.task, fingerprint=fingerprint).first()
            if existing is not None:
                return False
            PendingRelationDelivery.objects.create(
                task=pending.task,
                pending_relation=pending,
                fingerprint=fingerprint,
                payload_hash=payload_hash,
                source_model_id=pending.source_model_id,
                target_model_id=pending.target_model_id,
                relation_payload=pending.relation_payload,
            )
            return True
