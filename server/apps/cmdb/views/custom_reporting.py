import re

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError

from apps.cmdb.custom_reporting.extensions import get_custom_reporting_extension
from apps.cmdb.serializers.custom_reporting import CustomReportingCreateSerializer, CustomReportingIngestSerializer, CustomReportingUpdateSerializer
from apps.cmdb.views.mixins import CmdbPermissionMixin
from apps.core.decorators.api_permission import HasPermission
from apps.core.utils.open_base import OpenAPIViewSet
from apps.core.utils.web_utils import WebUtils


class CustomReportingTaskViewSet(CmdbPermissionMixin, viewsets.ViewSet):
    @staticmethod
    def _idempotency_key(request):
        raw_value = request.headers.get("Idempotency-Key")
        if raw_value is None:
            return None
        value = raw_value.strip()
        if not value:
            raise ValidationError({"Idempotency-Key": "不能为空白"})
        if len(value) > 255:
            raise ValidationError({"Idempotency-Key": "长度不能超过 255"})
        return value

    @staticmethod
    def _expected_version(request):
        raw_value = request.headers.get("If-Match")
        if raw_value is None:
            return None
        value = raw_value.strip()
        if not value:
            raise ValidationError({"If-Match": "不能为空白"})
        match = re.fullmatch(r'(?:(?:W/)?"(0|[1-9][0-9]*)"|(0|[1-9][0-9]*))', value)
        if match is None:
            raise ValidationError({"If-Match": "必须是非负状态版本"})
        return int(match.group(1) or match.group(2))

    @HasPermission("model_management-View")
    def list(self, request):
        return WebUtils.response_success(get_custom_reporting_extension().list_tasks(request, request.query_params.dict()))

    @action(detail=False, methods=["get"], url_path="stats")
    @HasPermission("model_management-View")
    def stats(self, request):
        return WebUtils.response_success(get_custom_reporting_extension().get_stats(request, request.query_params.dict()))

    @HasPermission("model_management-Add Model")
    def create(self, request):
        ser = CustomReportingCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        return WebUtils.response_success(
            get_custom_reporting_extension().create_task(request, ser.validated_data, idempotency_key=self._idempotency_key(request),)
        )

    @HasPermission("model_management-View")
    def retrieve(self, request, pk=None):
        return WebUtils.response_success(get_custom_reporting_extension().get_task(request, pk))

    @HasPermission("model_management-Edit Model")
    def update(self, request, pk=None):
        ser = CustomReportingUpdateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        return WebUtils.response_success(
            get_custom_reporting_extension().update_task(
                request, pk, ser.validated_data, idempotency_key=self._idempotency_key(request), expected_version=self._expected_version(request),
            )
        )

    @HasPermission("model_management-Delete Model")
    def destroy(self, request, pk=None):
        get_custom_reporting_extension().delete_task(request, pk)
        return WebUtils.response_success({})

    @action(detail=True, methods=["get"], url_path="field_registrations")
    @HasPermission("model_management-View")
    def field_registrations(self, request, pk=None):
        return WebUtils.response_success(get_custom_reporting_extension().get_field_registrations(request, pk))

    @action(detail=True, methods=["get"], url_path="batch_activity")
    @HasPermission("model_management-View")
    def batch_activity(self, request, pk=None):
        return WebUtils.response_success(get_custom_reporting_extension().get_batch_activity(request, pk))

    @action(detail=True, methods=["get"], url_path="onboarding_document")
    @HasPermission("model_management-View")
    def onboarding_document(self, request, pk=None):
        return WebUtils.response_success(get_custom_reporting_extension().get_onboarding_document(request, pk))

    @action(detail=True, methods=["post"], url_path="issue_credential")
    @HasPermission("model_management-Edit Model")
    def issue_credential(self, request, pk=None):
        return WebUtils.response_success(get_custom_reporting_extension().issue_credential(request, pk, request.data))

    @action(detail=True, methods=["post"], url_path="rotate_credential")
    @HasPermission("model_management-Edit Model")
    def rotate_credential(self, request, pk=None):
        credential_id = request.data.get("credential_id")
        if not credential_id:
            return WebUtils.response_error(error_message="credential_id is required", status_code=status.HTTP_400_BAD_REQUEST)
        return WebUtils.response_success(get_custom_reporting_extension().rotate_credential(request, pk, credential_id))

    @action(detail=True, methods=["post"], url_path="revoke_credential")
    @HasPermission("model_management-Edit Model")
    def revoke_credential(self, request, pk=None):
        credential_id = request.data.get("credential_id")
        if not credential_id:
            return WebUtils.response_error(error_message="credential_id is required", status_code=status.HTTP_400_BAD_REQUEST)
        return WebUtils.response_success(get_custom_reporting_extension().revoke_credential(request, pk, credential_id))

    @action(detail=True, methods=["post"], url_path=r"reviews/(?P<review_id>[^/]+)/approve")
    @HasPermission("model_management-Edit Model")
    def approve_review(self, request, pk=None, review_id=None):
        return WebUtils.response_success(get_custom_reporting_extension().approve_cleanup_review(request, pk, review_id))

    @action(detail=True, methods=["post"], url_path=r"reviews/(?P<review_id>[^/]+)/reject")
    @HasPermission("model_management-Edit Model")
    def reject_review(self, request, pk=None, review_id=None):
        return WebUtils.response_success(get_custom_reporting_extension().reject_cleanup_review(request, pk, review_id))


class CustomReportingIngestViewSet(OpenAPIViewSet):
    authentication_classes = []

    @staticmethod
    def _idempotency_key(request):
        raw_value = request.headers.get("Idempotency-Key")
        if raw_value is None:
            raise ValidationError({"Idempotency-Key": "为必填请求头"})
        value = raw_value.strip()
        if not value:
            raise ValidationError({"Idempotency-Key": "不能为空白"})
        if len(value) > 255:
            raise ValidationError({"Idempotency-Key": "长度不能超过 255"})
        return value

    def create(self, request):
        # 兼容两种写法：Authorization: Bearer <token> 或直接 Authorization: <token>
        auth = request.META.get("HTTP_AUTHORIZATION", "").strip()
        if auth[:7].lower() == "bearer ":
            token = auth[7:].strip()
        else:
            token = auth or None
        serializer = CustomReportingIngestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return WebUtils.response_success(
            get_custom_reporting_extension().ingest(request, token, serializer.validated_data, idempotency_key=self._idempotency_key(request),)
        )
