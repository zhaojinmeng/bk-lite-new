"""自定义上报（custom_reporting）商业域模型。

定位：商业 overlay 表，归属 cmdb_enterprise app。社区版不具备该能力，
读写由 ``apps.cmdb_enterprise`` 拥有。
"""

import hashlib
import secrets
import uuid
from hmac import compare_digest

from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone

from apps.core.models.maintainer_info import MaintainerInfo
from apps.core.models.time_info import TimeInfo


class CustomReportingTask(TimeInfo, MaintainerInfo):
    SYNC_ACTIVE = "active"
    SYNC_PROVISIONING = "provisioning"
    SYNC_UPDATING = "updating"
    SYNC_DEGRADED = "degraded"
    SYNC_STATUS_CHOICES = (
        (SYNC_ACTIVE, "已生效"),
        (SYNC_PROVISIONING, "创建中"),
        (SYNC_UPDATING, "同步中"),
        (SYNC_DEGRADED, "需人工处理"),
    )

    name = models.CharField(max_length=128, db_index=True, verbose_name="任务名称")
    team = models.JSONField(default=list, verbose_name="关联组织")
    desired_team = models.JSONField(default=list, verbose_name="期望组织")
    state_version = models.PositiveIntegerField(default=0, verbose_name="状态版本")
    sync_status = models.CharField(max_length=16, choices=SYNC_STATUS_CHOICES, default=SYNC_ACTIVE, db_index=True, verbose_name="同步状态",)
    provision_operation_id = models.UUIDField(null=True, blank=True, unique=True)
    config = models.JSONField(default=dict, verbose_name="任务配置")
    is_enabled = models.BooleanField(default=True, db_index=True, verbose_name="启用状态")
    last_reported_at = models.DateTimeField(null=True, blank=True, db_index=True, verbose_name="最近上报时间",)

    class Meta:
        app_label = "cmdb_enterprise"
        db_table = "cmdb_custom_reporting_task"
        verbose_name = "自定义报表任务"
        verbose_name_plural = verbose_name
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["name"], name="idx_custom_report_task_name"),
            models.Index(fields=["is_enabled"], name="idx_custom_report_task_enabled"),
        ]

    def __str__(self):
        return f"CustomReportingTask({self.id}:{self.name})"

    def sync_scopes(self):
        CustomReportingTaskScope.objects.filter(task=self, is_effective=True).delete()
        scopes = [CustomReportingTaskScope(task=self, team_id=team_id, name=self.name) for team_id in (self.team or [])]
        if scopes:
            CustomReportingTaskScope.objects.bulk_create(scopes)

    def save(self, *args, **kwargs):
        sync_scopes = kwargs.pop("sync_scopes", True)
        update_fields = kwargs.get("update_fields")
        if self._state.adding and not self.desired_team:
            self.desired_team = list(self.team or [])
        elif self.sync_status == self.SYNC_ACTIVE and (update_fields is None or "team" in update_fields):
            self.desired_team = list(self.team or [])
        with transaction.atomic():
            super().save(*args, **kwargs)
            if sync_scopes:
                self.sync_scopes()


class CustomReportingTaskScope(models.Model):
    task = models.ForeignKey(CustomReportingTask, on_delete=models.CASCADE, related_name="scopes", verbose_name="所属任务",)
    team_id = models.BigIntegerField(db_index=True, verbose_name="组织ID")
    name = models.CharField(max_length=128, verbose_name="任务名称")
    is_effective = models.BooleanField(default=True, db_index=True, verbose_name="是否已生效")
    reservation_operation_id = models.UUIDField(null=True, blank=True, db_index=True, verbose_name="预留操作ID")

    class Meta:
        app_label = "cmdb_enterprise"
        db_table = "cmdb_custom_reporting_task_scope"
        verbose_name = "自定义报表任务组织映射"
        verbose_name_plural = verbose_name
        unique_together = (("team_id", "name"),)
        constraints = [
            models.CheckConstraint(
                check=(Q(is_effective=True, reservation_operation_id__isnull=True) | Q(is_effective=False, reservation_operation_id__isnull=False)),
                name="chk_cr_scope_effective_reservation",
            ),
        ]
        indexes = [
            models.Index(fields=["task", "team_id"], name="idx_cr_task_scope_task_team"),
        ]

    def __str__(self):
        return f"CustomReportingTaskScope(task={self.task_id}, team={self.team_id}, name={self.name})"


class CustomReportingCredential(TimeInfo, MaintainerInfo):
    TOKEN_LOOKUP_PREFIX_LENGTH = 16

    task = models.ForeignKey(CustomReportingTask, on_delete=models.CASCADE, related_name="credentials", verbose_name="所属任务",)
    name = models.CharField(max_length=128, verbose_name="凭据名称")
    credential_type = models.CharField(max_length=64, verbose_name="凭据类型")
    credential_data = models.JSONField(default=dict, verbose_name="凭据内容")
    token_lookup = models.CharField(max_length=16, blank=True, default="", db_index=True, verbose_name="令牌查询前缀")
    is_enabled = models.BooleanField(default=True, verbose_name="启用状态")
    last_used_at = models.DateTimeField(null=True, blank=True, verbose_name="最近使用时间")

    class Meta:
        app_label = "cmdb_enterprise"
        db_table = "cmdb_custom_reporting_credential"
        verbose_name = "自定义报表凭据"
        verbose_name_plural = verbose_name
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["task"], name="uniq_cr_credential_task"),
        ]

    def __str__(self):
        return f"CustomReportingCredential({self.id}:{self.name})"

    def _sanitize_credential_data(self):
        credential_data = dict(self.credential_data or {})
        raw_token = credential_data.pop("token", None)
        if raw_token:
            token_hash = hashlib.sha256(str(raw_token).encode("utf-8")).hexdigest()
            credential_data["token_hash"] = token_hash
            credential_data["token_masked"] = True
            self.token_lookup = token_hash[: self.TOKEN_LOOKUP_PREFIX_LENGTH]
        elif credential_data.get("token_hash"):
            self.token_lookup = str(credential_data["token_hash"])[: self.TOKEN_LOOKUP_PREFIX_LENGTH]
        else:
            self.token_lookup = ""
        self.credential_data = credential_data

    def save(self, *args, **kwargs):
        self._sanitize_credential_data()
        super().save(*args, **kwargs)

    def issue_token(self, token: str | None = None):
        raw_token = token or secrets.token_urlsafe(32)
        credential_data = dict(self.credential_data or {})
        credential_data["token"] = raw_token
        credential_data["issued_at"] = timezone.now().isoformat()
        credential_data["token_revoked"] = False
        credential_data.pop("revoked_at", None)
        self.is_enabled = True
        self.credential_data = credential_data
        self.save()
        return raw_token

    def rotate_token(self, token: str | None = None):
        raw_token = self.issue_token(token=token)
        credential_data = dict(self.credential_data or {})
        credential_data["rotated_at"] = timezone.now().isoformat()
        self.credential_data = credential_data
        self.save()
        return raw_token

    def revoke_token(self):
        credential_data = dict(self.credential_data or {})
        credential_data.pop("token_hash", None)
        credential_data.pop("token_masked", None)
        credential_data["token_revoked"] = True
        credential_data["revoked_at"] = timezone.now().isoformat()
        self.is_enabled = False
        self.token_lookup = ""
        self.credential_data = credential_data
        self.save()

    def matches_token(self, raw_token: str | None):
        if not raw_token or not self.is_enabled:
            return False

        credential_data = dict(self.credential_data or {})
        token_hash = credential_data.get("token_hash")
        if not token_hash or credential_data.get("token_revoked") is True:
            return False

        expected_hash = hashlib.sha256(str(raw_token).encode("utf-8")).hexdigest()
        return compare_digest(str(token_hash), expected_hash)

    def mark_used(self):
        self.last_used_at = timezone.now()
        self.save(update_fields=["last_used_at", "updated_at"])


class CustomReportingBatch(TimeInfo):
    STATUS_PENDING = "pending"
    STATUS_RUNNING = "running"
    STATUS_SUCCESS = "success"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = (
        (STATUS_PENDING, "待处理"),
        (STATUS_RUNNING, "执行中"),
        (STATUS_SUCCESS, "成功"),
        (STATUS_FAILED, "失败"),
    )

    task = models.ForeignKey(CustomReportingTask, on_delete=models.CASCADE, related_name="batches", verbose_name="所属任务",)
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True, verbose_name="批次状态",)
    summary = models.JSONField(default=dict, verbose_name="批次摘要")

    class Meta:
        app_label = "cmdb_enterprise"
        db_table = "cmdb_custom_reporting_batch"
        verbose_name = "自定义报表批次"
        verbose_name_plural = verbose_name
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["task", "status"], name="idx_custom_report_batch_status"),
        ]

    def __str__(self):
        return f"CustomReportingBatch({self.id}:{self.status})"


class CustomReportingPendingRelation(TimeInfo):
    task = models.ForeignKey(CustomReportingTask, on_delete=models.CASCADE, related_name="pending_relations", verbose_name="所属任务",)
    source_model_id = models.CharField(max_length=64, verbose_name="源模型ID")
    target_model_id = models.CharField(max_length=64, verbose_name="目标模型ID")
    relation_payload = models.JSONField(default=dict, verbose_name="关系载荷")

    class Meta:
        app_label = "cmdb_enterprise"
        db_table = "cmdb_custom_reporting_pending_relation"
        verbose_name = "自定义报表待处理关系"
        verbose_name_plural = verbose_name
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["task", "source_model_id", "target_model_id"], name="idx_cr_pending_rel",),
        ]

    def __str__(self):
        return f"CustomReportingPendingRelation(" f"{self.id}:{self.source_model_id}->{self.target_model_id})"


class PendingRelationDelivery(TimeInfo):
    STATE_PENDING = "pending"
    STATE_SENDING = "sending"
    STATE_RETRY = "retry"
    STATE_SUCCESS = "success"
    STATE_DEAD_LETTER = "dead_letter"
    STATE_CHOICES = (
        (STATE_PENDING, "待投递"),
        (STATE_SENDING, "投递中"),
        (STATE_RETRY, "等待重试"),
        (STATE_SUCCESS, "投递成功"),
        (STATE_DEAD_LETTER, "死信"),
    )

    task = models.ForeignKey(CustomReportingTask, on_delete=models.CASCADE, related_name="pending_relation_deliveries", verbose_name="所属任务",)
    pending_relation = models.OneToOneField(
        CustomReportingPendingRelation, null=True, blank=True, on_delete=models.SET_NULL, related_name="delivery",
    )
    fingerprint = models.CharField(max_length=64)
    payload_hash = models.CharField(max_length=64)
    source_model_id = models.CharField(max_length=64)
    target_model_id = models.CharField(max_length=64)
    relation_payload = models.JSONField(default=dict)
    state = models.CharField(max_length=32, choices=STATE_CHOICES, default=STATE_PENDING, db_index=True)
    attempt_count = models.PositiveIntegerField(default=0)
    next_retry_at = models.DateTimeField(default=timezone.now, db_index=True)
    owner_token = models.CharField(max_length=64, blank=True, default="")
    lease_expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    generation = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True, default="")

    class Meta:
        app_label = "cmdb_enterprise"
        db_table = "cmdb_pending_relation_delivery"
        ordering = ["next_retry_at", "id"]
        constraints = [
            models.UniqueConstraint(fields=["task", "fingerprint"], name="uniq_cr_delivery_fprint"),
        ]
        indexes = [
            models.Index(fields=["state", "next_retry_at"], name="idx_cr_delivery_ready"),
            models.Index(fields=["state", "lease_expires_at"], name="idx_cr_delivery_lease"),
        ]


class CustomReportingCleanupReview(TimeInfo, MaintainerInfo):
    STATUS_PENDING = "pending"
    STATUS_APPROVING = "approving"
    STATUS_APPROVED = "approved"
    STATUS_REJECTED = "rejected"
    STATUS_CHOICES = (
        (STATUS_PENDING, "待审核"),
        (STATUS_APPROVING, "审核执行中"),
        (STATUS_APPROVED, "已通过"),
        (STATUS_REJECTED, "已驳回"),
    )

    batch = models.ForeignKey(CustomReportingBatch, on_delete=models.CASCADE, related_name="cleanup_reviews", verbose_name="所属批次",)
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True, verbose_name="审核状态",)
    review_payload = models.JSONField(default=dict, verbose_name="审核内容")
    reviewed_by = models.CharField(max_length=32, blank=True, default="", verbose_name="审核人")
    reviewed_at = models.DateTimeField(null=True, blank=True, verbose_name="审核时间")

    class Meta:
        app_label = "cmdb_enterprise"
        db_table = "cmdb_custom_reporting_cleanup_review"
        verbose_name = "自定义报表清理审核"
        verbose_name_plural = verbose_name
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["batch", "status"], name="idx_cr_review_status"),
        ]

    def __str__(self):
        return f"CustomReportingCleanupReview({self.id}:batch={self.batch_id})"


class CustomReportingFieldRegistration(TimeInfo):
    """快速模型字段登记元数据：首次出现时间 / 推荐类型 / 是否未定型。

    身份键等创建时声明的字段不入此表（视为已定型）；由首次上报数据自动登记的
    新字段在此记录，供"字段登记情况"展示与后续校正。
    """

    model_id = models.CharField(max_length=64, db_index=True, verbose_name="目标模型ID")
    attr_id = models.CharField(max_length=128, verbose_name="字段ID")
    recommended_type = models.CharField(max_length=32, default="string", verbose_name="推荐类型")
    is_undefined = models.BooleanField(default=True, verbose_name="未定型")
    first_seen_at = models.DateTimeField(verbose_name="首次出现时间")

    class Meta:
        app_label = "cmdb_enterprise"
        db_table = "cmdb_custom_reporting_field_registration"
        verbose_name = "自定义报表字段登记"
        verbose_name_plural = verbose_name
        ordering = ["first_seen_at"]
        constraints = [
            models.UniqueConstraint(fields=["model_id", "attr_id"], name="uniq_cr_field_reg_model_attr"),
        ]

    def __str__(self):
        return f"CustomReportingFieldRegistration({self.model_id}.{self.attr_id})"


class CustomReportingOperationState(models.TextChoices):
    PENDING = "pending", "待处理"
    CLAIMED = "claimed", "已认领"
    GRAPH_WRITING = "graph_writing", "图写入中"
    GRAPH_APPLIED = "graph_applied", "图事实已应用"
    DB_COMMITTED = "db_committed", "关系库已提交"
    POST_ACTIONS_PENDING = "post_actions_pending", "等待后置动作"
    COMPLETED = "completed", "已完成"
    RETRY = "retry", "等待重试"
    COMPENSATING = "compensating", "补偿中"
    MANUAL_FAILED = "manual_failed", "需人工处理"


class CustomReportingOutboxState(models.TextChoices):
    PENDING = "pending", "等待投递"
    SENDING = "sending", "投递中"
    RETRY = "retry", "等待重试"
    SUCCESS = "success", "投递成功"
    FAILED = "failed", "投递失败"


class CustomReportingOperation(TimeInfo):
    operation_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    action = models.CharField(max_length=64)
    scope_key = models.CharField(max_length=255)
    idempotency_key = models.CharField(max_length=255)
    request_hash = models.CharField(max_length=64)
    generation = models.PositiveIntegerField(default=0)
    state = models.CharField(max_length=32, choices=CustomReportingOperationState.choices, default=CustomReportingOperationState.PENDING,)
    owner_token = models.CharField(max_length=64, blank=True, default="")
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    desired_snapshot = models.JSONField(default=dict, blank=True)
    fact_snapshot = models.JSONField(default=dict, blank=True)
    result_summary = models.JSONField(default=dict, blank=True)
    attempt_count = models.PositiveIntegerField(default=0)
    next_attempt_at = models.DateTimeField(default=timezone.now)
    last_error = models.TextField(blank=True, default="")

    class Meta:
        app_label = "cmdb_enterprise"
        db_table = "cmdb_custom_reporting_operation"
        constraints = [
            models.UniqueConstraint(fields=["scope_key", "idempotency_key"], name="uniq_cr_operation_scope_idem",),
        ]
        indexes = [
            models.Index(fields=["state", "next_attempt_at"], name="idx_cr_operation_ready"),
            models.Index(fields=["state", "lease_expires_at"], name="idx_cr_operation_lease"),
        ]

    def __str__(self):
        return f"CustomReportingOperation({self.operation_id}:{self.state})"


class CustomReportingOutbox(TimeInfo):
    event_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    operation = models.ForeignKey(CustomReportingOperation, on_delete=models.CASCADE, related_name="outbox_events",)
    event_type = models.CharField(max_length=64)
    dedupe_key = models.CharField(max_length=255)
    payload = models.JSONField(default=dict, blank=True)
    state = models.CharField(max_length=16, choices=CustomReportingOutboxState.choices, default=CustomReportingOutboxState.PENDING,)
    attempt_count = models.PositiveIntegerField(default=0)
    owner_token = models.CharField(max_length=64, blank=True, default="")
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    next_retry_at = models.DateTimeField(default=timezone.now)
    last_error = models.TextField(blank=True, default="")

    class Meta:
        app_label = "cmdb_enterprise"
        db_table = "cmdb_custom_reporting_outbox"
        constraints = [
            models.UniqueConstraint(fields=["operation", "event_type", "dedupe_key"], name="uniq_cr_outbox_event_dedupe",),
        ]
        indexes = [
            models.Index(fields=["state", "next_retry_at"], name="idx_cr_outbox_ready"),
            models.Index(fields=["state", "lease_expires_at"], name="idx_cr_outbox_lease"),
        ]

    def __str__(self):
        return f"CustomReportingOutbox({self.event_id}:{self.state})"
