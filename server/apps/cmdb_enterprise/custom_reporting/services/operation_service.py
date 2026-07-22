import hashlib
import json
from dataclasses import dataclass
from datetime import timedelta
from time import sleep

from django.db import IntegrityError, OperationalError, connection, transaction
from django.db.models import F, Q
from django.utils.timezone import now

from apps.cmdb_enterprise.custom_reporting.models import (
    CustomReportingOperation,
    CustomReportingOperationState,
    CustomReportingOutbox,
    CustomReportingOutboxState,
)
from apps.cmdb_enterprise.custom_reporting.services.resource_budget import RESOURCE_BUDGET


class OperationConflict(RuntimeError):
    pass


@dataclass(frozen=True)
class OperationStart:
    operation: CustomReportingOperation
    reused: bool


@dataclass(frozen=True)
class OperationLease:
    operation_id: object
    generation: int
    owner_token: str
    lease_expires_at: object
    attempt_count: int


@dataclass(frozen=True)
class OutboxLease:
    event_id: object
    owner_token: str
    lease_expires_at: object
    attempt_count: int


class CustomReportingOperationService:
    CLEANUP_APPROVE_ACTION = "cleanup_review_approve"
    INGEST_ACTION = "ingest"
    DEFAULT_LEASE_SECONDS = 300
    DEFAULT_RETRY_BASE_SECONDS = 30
    MAX_RETRY_DELAY_SECONDS = 3600
    SQLITE_WINNER_RETRY_DELAYS = (0.0, 0.005, 0.01, 0.02, 0.04, 0.08)
    TERMINAL_STATES = frozenset({CustomReportingOperationState.COMPLETED, CustomReportingOperationState.MANUAL_FAILED})
    IN_PROGRESS_STATES = frozenset(
        {
            CustomReportingOperationState.CLAIMED,
            CustomReportingOperationState.GRAPH_WRITING,
            CustomReportingOperationState.GRAPH_APPLIED,
            CustomReportingOperationState.DB_COMMITTED,
            CustomReportingOperationState.POST_ACTIONS_PENDING,
        }
    )

    @staticmethod
    def _canonical_json_bytes(value) -> bytes:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,).encode("utf-8")

    @classmethod
    def request_hash(cls, *, action: str, desired_snapshot: dict) -> str:
        request_bytes = cls._canonical_json_bytes({"action": action, "desired_snapshot": desired_snapshot})
        return hashlib.sha256(request_bytes).hexdigest()

    @staticmethod
    def _operation_error(error: Exception) -> str:
        return f"{error.__class__.__name__}: 自定义上报操作执行失败"

    @staticmethod
    def _outbox_error(error: Exception) -> str:
        return f"{error.__class__.__name__}: 自定义上报后置事件投递失败"

    @staticmethod
    def _retry_delay(attempt_count: int, *, base_seconds: int, max_seconds: int) -> int:
        base = max(1, int(base_seconds))
        maximum = max(base, int(max_seconds))
        return min(maximum, base * (2 ** max(0, attempt_count - 1)))

    @staticmethod
    def _is_sqlite_lock_error(error: OperationalError) -> bool:
        message = str(error).lower()
        return connection.vendor == "sqlite" and ("locked" in message or "busy" in message)

    @classmethod
    def _winner_after_conflict(cls, *, model, lookup: dict, conflict: Exception):
        if isinstance(conflict, IntegrityError):
            try:
                return model.objects.get(**lookup)
            except model.DoesNotExist:
                raise conflict

        if not cls._is_sqlite_lock_error(conflict):
            raise conflict
        for delay in cls.SQLITE_WINNER_RETRY_DELAYS:
            if delay:
                sleep(delay)
            try:
                return model.objects.get(**lookup)
            except model.DoesNotExist:
                continue
            except OperationalError as read_error:
                if not cls._is_sqlite_lock_error(read_error):
                    raise
        raise conflict

    @classmethod
    def start(cls, *, scope_key: str, idempotency_key: str, action: str, desired_snapshot: dict,) -> OperationStart:
        request_hash = cls.request_hash(action=action, desired_snapshot=desired_snapshot)
        try:
            operation, created = CustomReportingOperation.objects.get_or_create(
                scope_key=scope_key,
                idempotency_key=idempotency_key,
                defaults={"action": action, "request_hash": request_hash, "desired_snapshot": desired_snapshot},
            )
        except (IntegrityError, OperationalError) as conflict:
            operation = cls._winner_after_conflict(
                model=CustomReportingOperation, lookup={"scope_key": scope_key, "idempotency_key": idempotency_key}, conflict=conflict,
            )
            created = False

        if operation.request_hash != request_hash:
            raise OperationConflict("同一 scope 的幂等键已用于不同请求")
        return OperationStart(operation=operation, reused=not created)

    @classmethod
    def start_cleanup_approval(cls, *, review_id: int, desired_snapshot: dict) -> OperationStart:
        """为一个清理审核创建唯一、可恢复的批准 Operation。"""
        return cls.start(
            scope_key=f"cleanup_review:{int(review_id)}",
            idempotency_key="approve",
            action=cls.CLEANUP_APPROVE_ACTION,
            desired_snapshot=desired_snapshot,
        )

    @classmethod
    def start_ingest(cls, *, task_id: int, idempotency_key: str, payload: dict) -> OperationStart:
        return cls.start(
            scope_key=f"ingest_task:{int(task_id)}",
            idempotency_key=idempotency_key,
            action=cls.INGEST_ACTION,
            desired_snapshot={"task_id": int(task_id), "payload": payload},
        )

    @classmethod
    def claim(cls, operation_id, *, owner_token: str, lease_seconds: int | None = None, force: bool = False,) -> OperationLease | None:
        last_lock_error = None
        cas_retry_index = 0
        for cas_retry_index, delay in enumerate(cls.SQLITE_WINNER_RETRY_DELAYS):
            if delay:
                sleep(delay)
            current_time = now()
            try:
                operation = (
                    CustomReportingOperation.objects.filter(operation_id=operation_id)
                    .values("id", "operation_id", "state", "generation", "owner_token", "lease_expires_at", "next_attempt_at", "attempt_count",)
                    .first()
                )
                if not operation:
                    return None

                filters = {
                    "id": operation["id"],
                    "state": operation["state"],
                    "generation": operation["generation"],
                    "owner_token": operation["owner_token"],
                    "lease_expires_at": operation["lease_expires_at"],
                }
                if operation["state"] in {
                    CustomReportingOperationState.PENDING,
                    CustomReportingOperationState.RETRY,
                }:
                    if not force and operation["next_attempt_at"] > current_time:
                        return None
                    filters["next_attempt_at"] = operation["next_attempt_at"]
                elif operation["state"] in cls.IN_PROGRESS_STATES:
                    if operation["lease_expires_at"] is None or operation["lease_expires_at"] > current_time:
                        return None
                else:
                    return None

                lease_until = current_time + timedelta(seconds=max(1, int(lease_seconds or cls.DEFAULT_LEASE_SECONDS)))
                updated = CustomReportingOperation.objects.filter(**filters).update(
                    state=CustomReportingOperationState.CLAIMED,
                    generation=F("generation") + 1,
                    attempt_count=F("attempt_count") + 1,
                    owner_token=owner_token,
                    lease_expires_at=lease_until,
                    last_error="",
                    updated_at=current_time,
                )
            except OperationalError as error:
                if not cls._is_sqlite_lock_error(error):
                    raise
                last_lock_error = error
                continue
            if not updated:
                return None
            break
        else:
            raise last_lock_error

        # CAS 已成功后不能从头重试，否则会把本 worker 已持有的 lease 误判为他人占用。
        remaining_delays = (0.0, *cls.SQLITE_WINNER_RETRY_DELAYS[cas_retry_index + 1 :])
        for delay in remaining_delays:
            if delay:
                sleep(delay)
            try:
                claimed = CustomReportingOperation.objects.values(
                    "operation_id", "generation", "owner_token", "lease_expires_at", "attempt_count",
                ).get(
                    id=operation["id"], state=CustomReportingOperationState.CLAIMED, generation=operation["generation"] + 1, owner_token=owner_token,
                )
                return OperationLease(**claimed)
            except OperationalError as error:
                if not cls._is_sqlite_lock_error(error):
                    raise
                last_lock_error = error
        raise last_lock_error

    @classmethod
    def transition(
        cls,
        operation_id,
        *,
        generation: int,
        owner_token: str,
        from_state: str,
        to_state: str,
        fact_snapshot: dict | None = None,
        result_summary: dict | None = None,
    ) -> bool:
        current_time = now()
        updates = {"state": to_state, "updated_at": current_time}
        if fact_snapshot is not None:
            updates["fact_snapshot"] = fact_snapshot
        if result_summary is not None:
            updates["result_summary"] = result_summary
        return bool(
            CustomReportingOperation.objects.filter(
                operation_id=operation_id, state=from_state, generation=generation, owner_token=owner_token, lease_expires_at__gt=current_time,
            ).update(**updates)
        )

    @classmethod
    def record_fact_snapshot(cls, operation_id, *, generation: int, owner_token: str, state: str, fact_snapshot: dict,) -> bool:
        current_time = now()
        return bool(
            CustomReportingOperation.objects.filter(
                operation_id=operation_id, state=state, generation=generation, owner_token=owner_token, lease_expires_at__gt=current_time,
            ).update(fact_snapshot=fact_snapshot, updated_at=current_time)
        )

    @classmethod
    def finalize(
        cls, operation_id, *, generation: int, owner_token: str, state: str, fact_snapshot: dict | None = None, result_summary: dict | None = None,
    ) -> bool:
        if state not in cls.TERMINAL_STATES:
            raise ValueError(f"不允许 finalize 到非终态: {state}")
        current_time = now()
        updates = {
            "state": state,
            "owner_token": "",
            "lease_expires_at": None,
            "last_error": "",
            "updated_at": current_time,
        }
        if fact_snapshot is not None:
            updates["fact_snapshot"] = fact_snapshot
        if result_summary is not None:
            updates["result_summary"] = result_summary
        return bool(
            CustomReportingOperation.objects.filter(
                operation_id=operation_id,
                state__in=cls.IN_PROGRESS_STATES,
                generation=generation,
                owner_token=owner_token,
                lease_expires_at__gt=current_time,
            ).update(**updates)
        )

    @classmethod
    def schedule_retry(
        cls,
        operation_id,
        *,
        generation: int,
        owner_token: str,
        error: Exception,
        base_delay_seconds: int | None = None,
        max_delay_seconds: int | None = None,
    ) -> bool:
        current_time = now()
        operation = CustomReportingOperation.objects.filter(
            operation_id=operation_id,
            state__in=cls.IN_PROGRESS_STATES,
            generation=generation,
            owner_token=owner_token,
            lease_expires_at__gt=current_time,
        ).first()
        if not operation:
            return False
        delay = cls._retry_delay(
            operation.attempt_count,
            base_seconds=base_delay_seconds or cls.DEFAULT_RETRY_BASE_SECONDS,
            max_seconds=max_delay_seconds or cls.MAX_RETRY_DELAY_SECONDS,
        )
        return bool(
            CustomReportingOperation.objects.filter(
                id=operation.id,
                state=operation.state,
                generation=generation,
                owner_token=owner_token,
                lease_expires_at=operation.lease_expires_at,
                lease_expires_at__gt=current_time,
            ).update(
                state=CustomReportingOperationState.RETRY,
                owner_token="",
                lease_expires_at=None,
                next_attempt_at=current_time + timedelta(seconds=delay),
                last_error=cls._operation_error(error),
                updated_at=current_time,
            )
        )

    @staticmethod
    def enqueue_outbox(*, operation: CustomReportingOperation, event_type: str, dedupe_key: str, payload: dict,) -> CustomReportingOutbox:
        try:
            event, _ = CustomReportingOutbox.objects.get_or_create(
                operation=operation, event_type=event_type, dedupe_key=dedupe_key, defaults={"payload": payload},
            )
        except (IntegrityError, OperationalError) as conflict:
            event = CustomReportingOperationService._winner_after_conflict(
                model=CustomReportingOutbox, lookup={"operation": operation, "event_type": event_type, "dedupe_key": dedupe_key}, conflict=conflict,
            )
        if CustomReportingOperationService._canonical_json_bytes(event.payload) != CustomReportingOperationService._canonical_json_bytes(payload):
            raise OperationConflict("Outbox dedupe key 已用于不同 payload")
        return event

    @classmethod
    def claim_outbox(cls, event_id, *, owner_token: str, lease_seconds: int | None = None,) -> OutboxLease | None:
        current_time = now()
        event = (
            CustomReportingOutbox.objects.filter(event_id=event_id)
            .values("id", "event_id", "state", "owner_token", "lease_expires_at", "next_retry_at", "attempt_count",)
            .first()
        )
        if not event:
            return None

        filters = {
            "id": event["id"],
            "state": event["state"],
            "owner_token": event["owner_token"],
            "lease_expires_at": event["lease_expires_at"],
        }
        if event["state"] in {
            CustomReportingOutboxState.PENDING,
            CustomReportingOutboxState.RETRY,
        }:
            if event["next_retry_at"] > current_time:
                return None
            filters["next_retry_at"] = event["next_retry_at"]
        elif event["state"] == CustomReportingOutboxState.SENDING:
            if event["lease_expires_at"] is None or event["lease_expires_at"] > current_time:
                return None
        else:
            return None

        lease_until = current_time + timedelta(seconds=max(1, int(lease_seconds or cls.DEFAULT_LEASE_SECONDS)))
        updated = CustomReportingOutbox.objects.filter(**filters).update(
            state=CustomReportingOutboxState.SENDING,
            owner_token=owner_token,
            lease_expires_at=lease_until,
            attempt_count=F("attempt_count") + 1,
            last_error="",
            updated_at=current_time,
        )
        if not updated:
            return None
        claimed = CustomReportingOutbox.objects.values("event_id", "owner_token", "lease_expires_at", "attempt_count",).get(
            id=event["id"], state=CustomReportingOutboxState.SENDING, owner_token=owner_token, attempt_count=event["attempt_count"] + 1,
        )
        return OutboxLease(**claimed)

    @staticmethod
    def finalize_outbox(event_id, *, owner_token: str, attempt_count: int) -> bool:
        current_time = now()
        return bool(
            CustomReportingOutbox.objects.filter(
                event_id=event_id,
                state=CustomReportingOutboxState.SENDING,
                owner_token=owner_token,
                attempt_count=attempt_count,
                lease_expires_at__gt=current_time,
            ).update(
                state=CustomReportingOutboxState.SUCCESS, owner_token="", lease_expires_at=None, last_error="", updated_at=current_time,
            )
        )

    @classmethod
    def finalize_outbox_and_complete_operation(cls, event_id, *, owner_token: str, attempt_count: int,) -> bool:
        """原子 ACK 单个事件，并在最后一个事件成功时收口所属 Operation。"""

        last_lock_error = None
        for delay in cls.SQLITE_WINNER_RETRY_DELAYS:
            if delay:
                sleep(delay)
            try:
                return cls._finalize_outbox_and_complete_operation_once(event_id, owner_token=owner_token, attempt_count=attempt_count,)
            except OperationalError as error:
                if not cls._is_sqlite_lock_error(error):
                    raise
                last_lock_error = error
        raise last_lock_error

    @classmethod
    def _finalize_outbox_and_complete_operation_once(cls, event_id, *, owner_token: str, attempt_count: int,) -> bool:
        current_time = now()
        with transaction.atomic():
            event = CustomReportingOutbox.objects.filter(event_id=event_id).values("id", "operation_id").first()
            if event is None:
                return False
            operation = CustomReportingOperation.objects.select_for_update().filter(id=event["operation_id"]).first()
            if operation is None:
                return False
            updated = CustomReportingOutbox.objects.filter(
                id=event["id"],
                state=CustomReportingOutboxState.SENDING,
                owner_token=owner_token,
                attempt_count=attempt_count,
                lease_expires_at__gt=current_time,
            ).update(state=CustomReportingOutboxState.SUCCESS, owner_token="", lease_expires_at=None, last_error="", updated_at=current_time,)
            if not updated:
                return False
            if CustomReportingOutbox.objects.filter(operation_id=operation.id).exclude(state=CustomReportingOutboxState.SUCCESS).exists():
                return True
            if operation.state == CustomReportingOperationState.COMPLETED:
                return True
            completed = CustomReportingOperation.objects.filter(id=operation.id, state=CustomReportingOperationState.POST_ACTIONS_PENDING,).update(
                state=CustomReportingOperationState.COMPLETED, owner_token="", lease_expires_at=None, last_error="", updated_at=current_time,
            )
            if not completed:
                transaction.set_rollback(True)
                return False
            return True

    @classmethod
    def schedule_outbox_retry(
        cls,
        event_id,
        *,
        owner_token: str,
        attempt_count: int,
        error: Exception,
        base_delay_seconds: int | None = None,
        max_delay_seconds: int | None = None,
    ) -> bool:
        current_time = now()
        event = CustomReportingOutbox.objects.filter(
            event_id=event_id,
            state=CustomReportingOutboxState.SENDING,
            owner_token=owner_token,
            attempt_count=attempt_count,
            lease_expires_at__gt=current_time,
        ).first()
        if not event:
            return False
        delay = cls._retry_delay(
            event.attempt_count,
            base_seconds=base_delay_seconds or cls.DEFAULT_RETRY_BASE_SECONDS,
            max_seconds=max_delay_seconds or cls.MAX_RETRY_DELAY_SECONDS,
        )
        return bool(
            CustomReportingOutbox.objects.filter(
                id=event.id,
                state=CustomReportingOutboxState.SENDING,
                owner_token=owner_token,
                attempt_count=attempt_count,
                lease_expires_at=event.lease_expires_at,
                lease_expires_at__gt=current_time,
            ).update(
                state=CustomReportingOutboxState.RETRY,
                owner_token="",
                lease_expires_at=None,
                next_retry_at=current_time + timedelta(seconds=delay),
                last_error=cls._outbox_error(error),
                updated_at=current_time,
            )
        )

    @classmethod
    def ready_outbox_event_ids(cls, *, event_types: tuple[str, ...] | None = None, batch_size: int = 100,) -> list:
        current_time = now()
        ready = CustomReportingOutbox.objects.filter(
            Q(state__in=[CustomReportingOutboxState.PENDING, CustomReportingOutboxState.RETRY], next_retry_at__lte=current_time)
            | Q(state=CustomReportingOutboxState.SENDING, lease_expires_at__lte=current_time)
        )
        if event_types is not None:
            ready = ready.filter(event_type__in=event_types)
        return list(ready.order_by("next_retry_at", "id").values_list("event_id", flat=True)[: RESOURCE_BUDGET.clamp_batch_size(batch_size)])

    @classmethod
    def ready_ingest_operation_ids(cls, *, batch_size: int = 100) -> list:
        current_time = now()
        ready = CustomReportingOperation.objects.filter(action=cls.INGEST_ACTION).filter(
            Q(state=CustomReportingOperationState.RETRY, next_attempt_at__lte=current_time)
            | Q(state__in=cls.IN_PROGRESS_STATES, lease_expires_at__lte=current_time,)
        )
        return list(ready.order_by("next_attempt_at", "id").values_list("operation_id", flat=True)[: RESOURCE_BUDGET.clamp_batch_size(batch_size)])

    @staticmethod
    def complete_post_actions(operation_uuid) -> bool:
        """所有事件成功后收口 Operation；事件 lease 是该转换的事实栅栏。"""

        with transaction.atomic():
            unfinished = CustomReportingOutbox.objects.filter(operation__operation_id=operation_uuid).exclude(
                state=CustomReportingOutboxState.SUCCESS
            )
            if unfinished.exists():
                return True
            operation = CustomReportingOperation.objects.filter(operation_id=operation_uuid).first()
            if operation is None:
                return False
            if operation.state == CustomReportingOperationState.COMPLETED:
                return True
            return bool(
                CustomReportingOperation.objects.filter(
                    operation_id=operation_uuid, state=CustomReportingOperationState.POST_ACTIONS_PENDING,
                ).update(
                    state=CustomReportingOperationState.COMPLETED, owner_token="", lease_expires_at=None, last_error="", updated_at=now(),
                )
            )
