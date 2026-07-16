from typing import NamedTuple
from uuid import uuid4

from apps.cmdb_enterprise.custom_reporting.models import CustomReportingCredential, CustomReportingTask


class TokenTask(NamedTuple):
    task: CustomReportingTask
    raw_token: str


def unique_crval_name(kind: str) -> str:
    return f"crval_{kind}_{uuid4().hex}"


def create_token_task(*, team=None, username="crval_factory") -> TokenTask:
    task = CustomReportingTask.objects.create(
        name=unique_crval_name("task"),
        team=[1] if team is None else list(team),
        config={
            "mode": "standard",
            "model_id": unique_crval_name("model"),
            "identity_keys": ["inst_name"],
        },
        is_enabled=True,
        created_by=username,
        updated_by=username,
    )
    credential = CustomReportingCredential.objects.create(
        task=task,
        name=unique_crval_name("credential"),
        credential_type="api_token",
        credential_data={},
        created_by=username,
        updated_by=username,
    )
    raw_token = credential.issue_token(unique_crval_name("token"))
    return TokenTask(task=task, raw_token=raw_token)
