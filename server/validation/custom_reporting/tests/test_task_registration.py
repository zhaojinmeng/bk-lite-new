import pytest

from validation.custom_reporting.tests.test_runtime_contracts import KnownProductDefect

EXPECTED_MISSING_EXPIRE_TASK = ["apps.cmdb_enterprise.custom_reporting.tasks.custom_reporting_expire_cleanup"]


@pytest.mark.xfail(strict=True, raises=KnownProductDefect, reason="CRV-F11")
def test_every_enterprise_beat_task_is_registered():
    from apps.cmdb_enterprise.config import CELERY_BEAT_SCHEDULE
    from apps.core.celery import app

    app.loader.import_default_modules()
    missing = sorted(item["task"] for item in CELERY_BEAT_SCHEDULE.values() if item["task"] not in app.tasks)

    if missing == EXPECTED_MISSING_EXPIRE_TASK:
        raise KnownProductDefect(f"CRV-F11: observed missing tasks {missing!r}")
    assert missing == []
