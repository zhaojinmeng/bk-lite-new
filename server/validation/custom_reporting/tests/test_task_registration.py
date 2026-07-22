EXPECTED_CUSTOM_REPORTING_BEAT_TASKS = {
    "custom_reporting_expire_cleanup": "apps.cmdb_enterprise.custom_reporting.tasks.custom_reporting_expire_cleanup",
    "custom_reporting_recover_ingest_operations": (
        "apps.cmdb_enterprise.custom_reporting.tasks.custom_reporting_recover_ingest_operations"
    ),
    "custom_reporting_process_ingest_outbox": (
        "apps.cmdb_enterprise.custom_reporting.tasks.custom_reporting_process_ingest_outbox"
    ),
    "custom_reporting_process_pending_relations": (
        "apps.cmdb_enterprise.custom_reporting.tasks.custom_reporting_process_pending_relations"
    ),
}


def test_custom_reporting_reconciler_beat_entries_are_present_and_bounded():
    from apps.cmdb_enterprise.config import CELERY_BEAT_SCHEDULE

    missing = sorted(set(EXPECTED_CUSTOM_REPORTING_BEAT_TASKS) - set(CELERY_BEAT_SCHEDULE))
    assert missing == []

    for name, task_path in EXPECTED_CUSTOM_REPORTING_BEAT_TASKS.items():
        entry = CELERY_BEAT_SCHEDULE[name]
        assert entry["task"] == task_path
        assert entry.get("schedule") is not None
        assert entry.get("options", {}).get("expires", 0) > 0
        if name != "custom_reporting_expire_cleanup":
            assert 0 < entry.get("kwargs", {}).get("batch_size", 0) <= 100


def test_every_enterprise_beat_task_is_registered():
    from apps.cmdb_enterprise.config import CELERY_BEAT_SCHEDULE
    from apps.core.celery import app

    app.loader.import_default_modules()
    missing = sorted(item["task"] for item in CELERY_BEAT_SCHEDULE.values() if item["task"] not in app.tasks)

    assert missing == []
