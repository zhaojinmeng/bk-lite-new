from unittest.mock import ANY, MagicMock, Mock, call

import pytest
from django.utils import timezone

from apps.cmdb.graph.format_type import FORMAT_TYPE_PARAMS, ParameterCollector
from apps.cmdb.services import instance as instance_service
from apps.cmdb.services.model import ModelManage
from apps.cmdb_enterprise.custom_reporting.models import (
    CustomReportingBatch,
    CustomReportingCleanupReview,
    CustomReportingOperation,
    CustomReportingCredential,
    CustomReportingPendingRelation,
    CustomReportingTask,
)
from apps.cmdb_enterprise.custom_reporting.services import (
    cleanup_service,
    ingest_service,
    merge_service,
    reconcile_service,
    relation_service,
    task_service,
)
from apps.core.exceptions.base_app_exception import BaseAppException
from validation.custom_reporting.tests.factories import create_token_task, unique_crval_name
from validation.custom_reporting.tests.test_runtime_contracts import KnownProductDefect, _assert_contract_or_known_defect


def _merge_result(**overrides):
    result = {
        "created": 0,
        "updated": 0,
        "deleted": 0,
        "errors": 0,
        "covered_ids": [],
        "old_data": [],
        "index": {},
    }
    result.update(overrides)
    return result


def _record_instance_queries(monkeypatch, foreign_instance, owned_source=None):
    filters_seen = []

    class RecordingGraph:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def query_entity(self, entity, filters, **kwargs):
            filters_seen.append(list(filters))
            if owned_source is not None and any(
                item["field"] == "id" for item in filters
            ):
                return [owned_source], 1
            # 图服务可能忽略/错误执行下推过滤；resolver 必须对返回事实二次裁剪。
            return [foreign_instance], 1

    monkeypatch.setattr(instance_service, "GraphClient", RecordingGraph)
    monkeypatch.setattr(relation_service, "GraphClient", RecordingGraph)
    return filters_seen


def _allow_owned_cleanup(monkeypatch):
    monkeypatch.setattr(
        cleanup_service,
        "_owned_instance_ids",
        lambda task, inst_ids: list(inst_ids),
    )


@pytest.mark.django_db
@pytest.mark.parametrize("invalid_mapping", [None, [], {}])
def test_relation_rejects_legacy_association_without_mapping_before_pending_or_edge(
    monkeypatch,
    invalid_mapping,
):
    token_task = create_token_task()
    model_id = token_task.task.config["model_id"]
    target_model = unique_crval_name("legacy_target")
    association_id = unique_crval_name("legacy_association")
    resolve_instance = Mock(return_value=None)
    edge_write = Mock()
    association = {
        "model_asst_id": association_id,
        "src_model_id": model_id,
        "dst_model_id": target_model,
        "asst_id": "legacy",
    }
    if invalid_mapping is not None:
        association["mapping"] = invalid_mapping
    monkeypatch.setattr(
        ModelManage,
        "model_association_info_search",
        lambda model_asst_id: association,
    )
    monkeypatch.setattr(relation_service, "_resolve_instance", resolve_instance)
    monkeypatch.setattr(relation_service, "_create_edge", edge_write)
    relation = {
        "source": {"_id": 1, "model_id": model_id},
        "target": {
            "model_id": target_model,
            "identity": {"inst_name": "target"},
        },
        "asst_id": association_id,
    }

    with pytest.raises(BaseAppException, match="mapping"):
        relation_service.process(
            token_task.task,
            [relation],
            {},
            "crval_validator",
        )

    resolve_instance.assert_not_called()
    edge_write.assert_not_called()
    assert not CustomReportingPendingRelation.objects.filter(task=token_task.task).exists()


@pytest.mark.django_db
@pytest.mark.parametrize("mismatched_endpoint", ["source", "target"])
def test_relation_rejects_association_endpoint_mismatch_before_resolution_or_pending(
    monkeypatch,
    mismatched_endpoint,
):
    token_task = create_token_task()
    model_id = token_task.task.config["model_id"]
    target_model = unique_crval_name("target")
    association_id = unique_crval_name("association")
    association = {
        "model_asst_id": association_id,
        "src_model_id": model_id,
        "dst_model_id": target_model,
        "asst_id": "connect",
        "mapping": "n:n",
    }
    association[f"{'src' if mismatched_endpoint == 'source' else 'dst'}_model_id"] = (
        unique_crval_name("other_model")
    )
    monkeypatch.setattr(
        ModelManage,
        "model_association_info_search",
        lambda model_asst_id: association,
    )
    resolve_instance = Mock(return_value=None)
    edge_write = Mock()
    monkeypatch.setattr(relation_service, "_resolve_instance", resolve_instance)
    monkeypatch.setattr(relation_service, "_create_edge", edge_write)
    relation = {
        "source": {"_id": 1, "model_id": model_id},
        "target": {
            "model_id": target_model,
            "identity": {"inst_name": "target"},
        },
        "asst_id": association_id,
    }

    with pytest.raises(BaseAppException, match="模型"):
        relation_service.process(
            token_task.task,
            [relation],
            {},
            "crval_validator",
        )

    resolve_instance.assert_not_called()
    edge_write.assert_not_called()
    assert not CustomReportingPendingRelation.objects.filter(task=token_task.task).exists()


@pytest.mark.django_db
def test_create_rejects_invalid_identity_before_model_or_db_side_effects(monkeypatch):
    bootstrap = Mock()
    monkeypatch.setattr(task_service.model_service, "bootstrap_model", bootstrap)
    payload = {
        "name": unique_crval_name("invalid_identity_create"),
        "team": [1],
        "config": {"mode": "quick", "identity_keys": []},
        "quick_model": {
            "model_id": unique_crval_name("model"),
            "model_name": "非法身份模型",
            "classification_id": "server",
            "identity_keys": [],
        },
    }

    with pytest.raises(BaseAppException, match="身份键"):
        task_service.create_task(payload, username="crval_validator")

    bootstrap.assert_not_called()
    assert not CustomReportingTask.objects.filter(name=payload["name"]).exists()


@pytest.mark.django_db
def test_update_rejects_invalid_effective_identity_before_task_or_model_side_effects(monkeypatch):
    token_task = create_token_task(mode="quick")
    sync_model = Mock()
    monkeypatch.setattr(task_service.model_service, "sync_model_group", sync_model)
    original_config = dict(token_task.task.config)

    with pytest.raises(BaseAppException, match="身份键"):
        task_service.update_task(
            token_task.task.id,
            {
                "config": {"identity_keys": ["inst_name", "inst_name"]},
                "quick_model": {
                    "model_id": token_task.task.config["model_id"],
                    "identity_keys": ["inst_name", "inst_name"],
                },
            },
            username="crval_validator",
        )

    token_task.task.refresh_from_db()
    assert token_task.task.config == original_config
    sync_model.assert_not_called()


@pytest.mark.django_db
def test_ingest_rejects_persisted_invalid_identity_before_batch_or_downstream_side_effects(
    monkeypatch,
):
    token_task = create_token_task(mode="quick", identity_keys=[])
    register_fields = Mock(return_value=[])
    merge_instances = Mock(return_value=_merge_result())
    monkeypatch.setattr(ingest_service.model_service, "register_model_fields", register_fields)
    monkeypatch.setattr(ingest_service.merge_service, "merge_instances", merge_instances)
    monkeypatch.setattr(
        ingest_service.relation_service,
        "process",
        lambda *args: {"pending": 0},
    )

    with pytest.raises(BaseAppException, match="身份键"):
        ingest_service.ingest(
            token_task.raw_token,
            {"instances": [{"inst_name": "a"}]},
            operator="crval_validator",
        )

    assert not CustomReportingBatch.objects.filter(task=token_task.task).exists()
    register_fields.assert_not_called()
    merge_instances.assert_not_called()


@pytest.mark.django_db
def test_create_rejects_invalid_mode_before_model_or_db_side_effects(monkeypatch):
    bootstrap = Mock()
    monkeypatch.setattr(task_service.model_service, "bootstrap_model", bootstrap)
    payload = {
        "name": unique_crval_name("invalid_mode_create"),
        "team": [1],
        "config": {"mode": "standrad", "identity_keys": ["inst_name"]},
    }

    with pytest.raises(BaseAppException, match="mode"):
        task_service.create_task(payload, username="crval_validator")

    bootstrap.assert_not_called()
    assert not CustomReportingTask.objects.filter(name=payload["name"]).exists()


@pytest.mark.django_db
def test_update_rejects_invalid_mode_before_task_or_model_side_effects(monkeypatch):
    token_task = create_token_task(mode="quick")
    sync_model = Mock()
    monkeypatch.setattr(task_service.model_service, "sync_model_group", sync_model)
    original_config = dict(token_task.task.config)

    with pytest.raises(BaseAppException, match="mode"):
        task_service.update_task(
            token_task.task.id,
            {"config": {"mode": "quik"}},
            username="crval_validator",
        )

    token_task.task.refresh_from_db()
    assert token_task.task.config == original_config
    sync_model.assert_not_called()


@pytest.mark.django_db
def test_ingest_rejects_invalid_mode_before_credential_batch_or_downstream_side_effects(
    monkeypatch,
):
    token_task = create_token_task(mode="standard")
    token_task.task.config["mode"] = "standrad"
    token_task.task.save(update_fields=["config", "updated_at"], sync_scopes=False)
    register_fields = Mock(return_value=[])
    merge_instances = Mock(return_value=_merge_result())
    monkeypatch.setattr(ingest_service.model_service, "register_model_fields", register_fields)
    monkeypatch.setattr(ingest_service.merge_service, "merge_instances", merge_instances)
    monkeypatch.setattr(
        ModelManage,
        "search_model_attr",
        lambda model_id: [{"attr_id": "inst_name", "attr_type": "str"}],
    )

    with pytest.raises(BaseAppException, match="mode"):
        ingest_service.ingest(
            token_task.raw_token,
            {"instances": [{"inst_name": "a"}]},
            operator="crval_validator",
        )

    credential = CustomReportingCredential.objects.get(task=token_task.task)
    assert credential.last_used_at is None
    assert not CustomReportingBatch.objects.filter(task=token_task.task).exists()
    register_fields.assert_not_called()
    merge_instances.assert_not_called()


@pytest.mark.django_db
def test_quick_registration_and_merge_share_sanitized_instances(monkeypatch):
    token_task = create_token_task(mode="quick")
    consumed = []
    monkeypatch.setattr(
        ModelManage,
        "search_model_attr",
        lambda model_id: [{"attr_id": "inst_name", "attr_type": "str"}],
    )
    monkeypatch.setattr(
        ingest_service.model_service,
        "register_model_fields",
        lambda model_id, instances, username="admin", declared_attr_ids=None: consumed.append(
            ("register", instances)
        )
        or [],
    )
    monkeypatch.setattr(
        ingest_service.merge_service,
        "merge_instances",
        lambda task, model_id, instances, operator, **kwargs: consumed.append(
            ("merge", instances)
        )
        or _merge_result(),
    )
    monkeypatch.setattr(
        ingest_service.relation_service,
        "process",
        lambda *args: {"pending": 0},
    )

    ingest_service.ingest(
        token_task.raw_token,
        {
            "instances": [
                {
                    "inst_name": "a",
                    "owner": "ops",
                    "_id": 9001,
                    "cr_last_reported_at": "caller-controlled",
                }
            ]
        },
        operator="crval_validator",
    )

    assert [kind for kind, _ in consumed] == ["register", "merge"]
    assert consumed[0][1] is consumed[1][1]
    assert consumed[0][1] == [{"inst_name": "a", "owner": "ops"}]


@pytest.mark.django_db
def test_normalized_duplicate_identity_rejected_before_batch_registration_or_graph(
    monkeypatch,
):
    token_task = create_token_task(mode="quick", identity_keys=["serial"])
    register_fields = Mock(return_value=[])
    graph_client = MagicMock()
    management = Mock()
    graph_client.return_value.__enter__.return_value.query_entity.return_value = ([], 0)
    management.return_value.add_list = []
    management.return_value.update_list = []
    management.return_value.add_inst.return_value = {"success": [], "failed": []}
    management.return_value.update_inst.return_value = {
        "success": [],
        "failed": [],
    }
    monkeypatch.setattr(
        ModelManage,
        "search_model_attr",
        lambda model_id: [{"attr_id": "serial", "attr_type": "int"}],
    )
    monkeypatch.setattr(ingest_service.model_service, "register_model_fields", register_fields)
    monkeypatch.setattr(merge_service, "GraphClient", graph_client)
    monkeypatch.setattr(merge_service, "Management", management)

    with pytest.raises(BaseAppException, match="重复身份"):
        ingest_service.ingest(
            token_task.raw_token,
            {"instances": [{"serial": "1"}, {"serial": 1}]},
            operator="crval_validator",
        )

    assert not CustomReportingBatch.objects.filter(task=token_task.task).exists()
    register_fields.assert_not_called()
    graph_client.assert_not_called()
    management.assert_not_called()


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("instance", "attrs"),
    [
        ({}, [{"attr_id": "serial", "attr_type": "int"}]),
        ({"serial": None}, [{"attr_id": "serial", "attr_type": "int"}]),
        ({"serial": ""}, [{"attr_id": "serial", "attr_type": "int"}]),
        ({"serial": "not-an-int"}, [{"attr_id": "serial", "attr_type": "int"}]),
        ({"serial": True}, [{"attr_id": "serial", "attr_type": "int"}]),
        ({"serial": 1.5}, [{"attr_id": "serial", "attr_type": "int"}]),
        ({"serial": "yes"}, [{"attr_id": "serial", "attr_type": "bool"}]),
        ({"serial": "not-a-float"}, [{"attr_id": "serial", "attr_type": "float"}]),
        ({"serial": True}, [{"attr_id": "serial", "attr_type": "float"}]),
        ({"serial": float("nan")}, [{"attr_id": "serial", "attr_type": "float"}]),
        ({"serial": float("inf")}, [{"attr_id": "serial", "attr_type": "float"}]),
        ({"serial": "-Infinity"}, [{"attr_id": "serial", "attr_type": "float"}]),
        ({"serial": "not-a-time"}, [{"attr_id": "serial", "attr_type": "time"}]),
        ({"serial": True}, [{"attr_id": "serial", "attr_type": "time"}]),
        ({"serial": []}, [{"attr_id": "serial", "attr_type": "str"}]),
        ({"serial": {}}, [{"attr_id": "serial", "attr_type": "str"}]),
        ({"serial": float("nan")}, [{"attr_id": "serial", "attr_type": "str"}]),
        ({"serial": ["a"]}, [{"attr_id": "serial", "attr_type": "list"}]),
        ({"serial": "a"}, []),
        ({"serial": "a"}, [{"attr_id": "serial"}]),
        ({"serial": "a"}, [{"attr_id": "serial", "attr_type": ""}]),
    ],
    ids=[
        "missing",
        "none",
        "empty",
        "invalid-int",
        "bool-as-int",
        "fractional-int",
        "invalid-bool",
        "invalid-float",
        "bool-as-float",
        "nan-float",
        "positive-infinity-float",
        "negative-infinity-string-float",
        "invalid-time",
        "bool-as-time",
        "list-as-str",
        "dict-as-str",
        "nan-as-str",
        "non-scalar",
        "missing-identity-attr",
        "missing-attr-type",
        "empty-attr-type",
    ],
)
def test_invalid_identity_value_rejected_before_credential_batch_or_downstream_side_effects(
    monkeypatch,
    instance,
    attrs,
):
    token_task = create_token_task(mode="quick", identity_keys=["serial"])
    register_fields = Mock(return_value=[])
    merge_instances = Mock(return_value=_merge_result())
    graph_client = MagicMock()
    management = Mock()
    monkeypatch.setattr(
        ModelManage,
        "search_model_attr",
        lambda model_id: attrs,
    )
    monkeypatch.setattr(ingest_service.model_service, "register_model_fields", register_fields)
    monkeypatch.setattr(ingest_service.merge_service, "merge_instances", merge_instances)
    monkeypatch.setattr(merge_service, "GraphClient", graph_client)
    monkeypatch.setattr(merge_service, "Management", management)

    with pytest.raises(BaseAppException, match="身份"):
        ingest_service.ingest(
            token_task.raw_token,
            {"instances": [instance]},
            operator="crval_validator",
        )

    credential = CustomReportingCredential.objects.get(task=token_task.task)
    assert credential.last_used_at is None
    assert not CustomReportingBatch.objects.filter(task=token_task.task).exists()
    register_fields.assert_not_called()
    merge_instances.assert_not_called()
    graph_client.assert_not_called()
    management.assert_not_called()


@pytest.mark.django_db
def test_missing_identity_metadata_rejected_before_side_effects_for_empty_batch(monkeypatch):
    token_task = create_token_task(mode="standard", identity_keys=["serial"])
    register_fields = Mock(return_value=[])
    merge_instances = Mock(return_value=_merge_result())
    graph_client = MagicMock()
    management = Mock()
    monkeypatch.setattr(ModelManage, "search_model_attr", lambda model_id: [])
    monkeypatch.setattr(ingest_service.model_service, "register_model_fields", register_fields)
    monkeypatch.setattr(ingest_service.merge_service, "merge_instances", merge_instances)
    monkeypatch.setattr(merge_service, "GraphClient", graph_client)
    monkeypatch.setattr(merge_service, "Management", management)

    with pytest.raises(BaseAppException, match="身份"):
        ingest_service.ingest(token_task.raw_token, {"instances": []})

    credential = CustomReportingCredential.objects.get(task=token_task.task)
    assert credential.last_used_at is None
    assert not CustomReportingBatch.objects.filter(task=token_task.task).exists()
    register_fields.assert_not_called()
    merge_instances.assert_not_called()
    graph_client.assert_not_called()
    management.assert_not_called()


@pytest.mark.django_db
def test_ingest_compiles_instances_once_and_real_merge_reuses_the_same_attr_snapshot(monkeypatch):
    token_task = create_token_task(mode="quick")
    attrs = [{"attr_id": "inst_name", "attr_type": "str"}]
    attr_lookup = Mock(side_effect=[attrs, AssertionError("属性快照被重复读取")])
    monkeypatch.setattr(ModelManage, "search_model_attr", attr_lookup)

    class EmptyGraph:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def query_entity(self, entity, filters, **kwargs):
            return [], 0

    class EmptyManagement:
        def __init__(self, **kwargs):
            self.add_list = []
            self.update_list = []

        def add_inst(self, items):
            return {"success": [], "failed": []}

        def update_inst(self, items):
            return {"success": [], "failed": []}

    monkeypatch.setattr(merge_service, "GraphClient", EmptyGraph)
    monkeypatch.setattr(merge_service, "Management", EmptyManagement)
    monkeypatch.setattr(
        ingest_service.relation_service,
        "process",
        lambda *args: {"pending": 0},
    )

    result = None
    try:
        result = ingest_service.ingest(
            token_task.raw_token,
            {"instances": [{"inst_name": "a"}]},
            operator="crval_validator",
        )
    except AssertionError:
        pass

    assert result is not None
    assert attr_lookup.call_count == 1
    batch = CustomReportingBatch.objects.get(task=token_task.task)
    assert batch.status == CustomReportingBatch.STATUS_SUCCESS


@pytest.mark.django_db
def test_partial_merge_marks_batch_failed_and_skips_snapshot(monkeypatch):
    token_task = create_token_task(cleanup_strategy="snapshot")
    CustomReportingPendingRelation.objects.create(
        task=token_task.task,
        source_model_id=token_task.task.config["model_id"],
        target_model_id="target",
        relation_payload={"asst_id": "pending"},
    )
    monkeypatch.setattr(
        ModelManage,
        "search_model_attr",
        lambda model_id: [{"attr_id": "inst_name", "attr_type": "str"}],
    )
    monkeypatch.setattr(
        ingest_service.merge_service,
        "merge_instances",
        lambda *args, **kwargs: _merge_result(
            created=1,
            errors=1,
            covered_ids=[1],
            old_data=[{"_id": 1}, {"_id": 2}],
        ),
    )
    relation_process = Mock(return_value={"pending": 0})
    relation_backfill = Mock()
    monkeypatch.setattr(ingest_service.relation_service, "process", relation_process)
    monkeypatch.setattr(ingest_service.relation_service, "backfill", relation_backfill)
    snapshot = Mock(return_value={"deleted": 1, "review_created": False})
    monkeypatch.setattr(cleanup_service, "apply_snapshot", snapshot)

    with pytest.raises(BaseAppException, match="部分失败"):
        ingest_service.ingest(
            token_task.raw_token,
            {"instances": [{"inst_name": "a"}, {"inst_name": "b"}]},
        )

    batch = CustomReportingBatch.objects.get(task=token_task.task)
    assert batch.status == CustomReportingBatch.STATUS_FAILED
    assert "error" in batch.summary
    assert "created" not in batch.summary
    relation_process.assert_not_called()
    relation_backfill.assert_not_called()
    snapshot.assert_not_called()


@pytest.mark.django_db
def test_merge_query_is_scoped_by_owner_and_team(monkeypatch):
    token_task = create_token_task(team=[1])
    model_id = token_task.task.config["model_id"]
    filters_seen = []

    class RecordingGraph:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def query_entity(self, entity, filters, **kwargs):
            filters_seen.extend(filters)
            return [], 0

    class EmptyManagement:
        def __init__(self, **kwargs):
            self.add_list = []
            self.update_list = []

        def add_inst(self, items):
            return {"success": [], "failed": []}

        def update_inst(self, items):
            return {"success": [], "failed": []}

    monkeypatch.setattr(merge_service, "GraphClient", RecordingGraph)
    monkeypatch.setattr(merge_service, "Management", EmptyManagement)
    monkeypatch.setattr(
        ModelManage,
        "search_model_attr",
        lambda model_id: [{"attr_id": "inst_name", "attr_type": "str"}],
    )

    merge_service.merge_instances(token_task.task, model_id, [], "crval_validator")

    model_filter = {"field": "model_id", "type": "str=", "value": model_id}
    owner_filter = {
        "field": "collect_task",
        "type": "str=",
        "value": f"cr_{token_task.task.id}",
    }
    organization_filter = {
        "field": "organization",
        "type": "list[]",
        "value": [1],
    }
    keyset_filter = {"field": "id", "type": "id>", "value": -1}
    collector = ParameterCollector()
    formatted = FORMAT_TYPE_PARAMS[organization_filter["type"]](organization_filter, collector)
    assert formatted == "ALL(x IN $list1 WHERE x IN n.organization)"
    assert collector.get_params() == {"list1": [1]}
    _assert_contract_or_known_defect(
        actual=filters_seen,
        expected=[model_filter, owner_filter, organization_filter, keyset_filter],
        known_bad=[model_filter],
        finding="CRV-F08",
    )


@pytest.mark.django_db
def test_snapshot_rejects_foreign_old_ids_before_delete(monkeypatch):
    token_task = create_token_task(team=[1], cleanup_strategy="snapshot")
    model_id = token_task.task.config["model_id"]
    batch = CustomReportingBatch.objects.create(
        task=token_task.task,
        status=CustomReportingBatch.STATUS_SUCCESS,
    )
    filters_seen = []
    owned = {
        "_id": 10,
        "model_id": model_id,
        "collect_task": f"cr_{token_task.task.id}",
        "organization": [1],
    }
    foreign = {
        "_id": 11,
        "model_id": model_id,
        "collect_task": "cr_foreign",
        "organization": [2],
    }

    class RecordingGraph:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def query_entity(self, entity, filters, **kwargs):
            filters_seen.append(list(filters))
            return [owned, foreign], 2

    monkeypatch.setattr(cleanup_service, "GraphClient", RecordingGraph)
    deleted = Mock()
    monkeypatch.setattr(cleanup_service, "_delete_instances", deleted)

    result = cleanup_service.apply_snapshot(
        token_task.task,
        batch,
        old_ids=[10, 11],
        covered_ids=[99],
        operator="crval_validator",
    )

    assert filters_seen
    assert all(
        {"model_id", "collect_task", "organization", "id"}
        <= {item["field"] for item in filters}
        for filters in filters_seen
    )
    deleted.assert_called_once_with([10], "crval_validator")
    assert result == {"deleted": 1, "review_created": False}


@pytest.mark.django_db
def test_snapshot_revalidates_owner_scope_immediately_before_delete(monkeypatch):
    token_task = create_token_task(team=[1], cleanup_strategy="snapshot")
    model_id = token_task.task.config["model_id"]
    batch = CustomReportingBatch.objects.create(
        task=token_task.task,
        status=CustomReportingBatch.STATUS_SUCCESS,
    )
    query_results = iter(
        [
            [
                {
                    "_id": 10,
                    "model_id": model_id,
                    "collect_task": f"cr_{token_task.task.id}",
                    "organization": [1],
                }
            ],
            [
                {
                    "_id": 10,
                    "model_id": model_id,
                    "collect_task": "cr_foreign_after_snapshot",
                    "organization": [1],
                }
            ],
        ]
    )

    class DriftingGraph:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def query_entity(self, entity, filters, **kwargs):
            result = next(query_results)
            return result, len(result)

    monkeypatch.setattr(cleanup_service, "GraphClient", DriftingGraph)
    deleted = Mock()
    monkeypatch.setattr(cleanup_service, "_delete_instances", deleted)

    result = cleanup_service.apply_snapshot(
        token_task.task,
        batch,
        old_ids=[10],
        covered_ids=[99],
        operator="crval_validator",
    )

    deleted.assert_not_called()
    assert result == {"deleted": 0, "review_created": False}


@pytest.mark.django_db
def test_expire_query_pushes_owner_scope_before_stale_filter(monkeypatch):
    token_task = create_token_task(team=[1], cleanup_strategy="expire")
    token_task.task.config["expire_days"] = 1
    token_task.task.save(update_fields=["config", "updated_at"], sync_scopes=False)
    filters_seen = []

    class RecordingGraph:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def query_entity(self, entity, filters, **kwargs):
            filters_seen.append(list(filters))
            return [], 0

    monkeypatch.setattr(cleanup_service, "GraphClient", RecordingGraph)

    cleanup_service.expire_cleanup(now=timezone.now())

    assert len(filters_seen) == 1
    assert [item["field"] for item in filters_seen[0]] == [
        "model_id",
        "collect_task",
        "organization",
        "id",
    ]


@pytest.mark.django_db
def test_review_approval_revalidates_owner_after_candidate_drift(monkeypatch):
    token_task = create_token_task(team=[1], cleanup_strategy="snapshot")
    model_id = token_task.task.config["model_id"]
    token_task.task.config["snapshot_delete_ratio_threshold"] = 1
    token_task.task.save(update_fields=["config", "updated_at"], sync_scopes=False)
    batch = CustomReportingBatch.objects.create(
        task=token_task.task,
        status=CustomReportingBatch.STATUS_SUCCESS,
    )
    query_results = iter(
        [
            [
                {
                    "_id": 10,
                    "model_id": model_id,
                    "collect_task": f"cr_{token_task.task.id}",
                    "organization": [1],
                }
            ],
            [
                {
                    "_id": 10,
                    "model_id": model_id,
                    "collect_task": "cr_foreign_after_review",
                    "organization": [1],
                }
            ],
        ]
    )

    class DriftingGraph:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def query_entity(self, entity, filters, **kwargs):
            result = next(query_results)
            return result, len(result)

    monkeypatch.setattr(cleanup_service, "GraphClient", DriftingGraph)
    deleted = Mock()
    monkeypatch.setattr(cleanup_service, "_delete_instances", deleted)

    snapshot = cleanup_service.apply_snapshot(
        token_task.task,
        batch,
        old_ids=[10],
        covered_ids=[99],
        operator="crval_validator",
    )
    review = CustomReportingCleanupReview.objects.get(batch=batch)
    cleanup_service.approve(token_task.task.id, review.id, "crval_validator")

    assert snapshot == {"deleted": 0, "review_created": True}
    deleted.assert_called_once_with([], "crval_validator", inst_list=[], record_change=False)


@pytest.mark.django_db
def test_expire_revalidates_owner_immediately_before_delete(monkeypatch):
    token_task = create_token_task(team=[1], cleanup_strategy="expire")
    model_id = token_task.task.config["model_id"]
    token_task.task.config["expire_days"] = 1
    token_task.task.save(update_fields=["config", "updated_at"], sync_scopes=False)
    stale_at = (timezone.now() - timezone.timedelta(days=2)).isoformat()
    query_results = iter(
        [
            [
                {
                    "_id": 10,
                    "model_id": model_id,
                    "collect_task": f"cr_{token_task.task.id}",
                    "organization": [1],
                    "cr_last_reported_at": stale_at,
                }
            ],
            [
                {
                    "_id": 10,
                    "model_id": model_id,
                    "collect_task": "cr_foreign_before_delete",
                    "organization": [1],
                    "cr_last_reported_at": stale_at,
                }
            ],
        ]
    )

    class DriftingGraph:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def query_entity(self, entity, filters, **kwargs):
            result = next(query_results)
            return result, len(result)

    monkeypatch.setattr(cleanup_service, "GraphClient", DriftingGraph)
    deleted = Mock()
    monkeypatch.setattr(cleanup_service, "_delete_instances", deleted)

    cleanup_service.expire_cleanup(now=timezone.now())

    deleted.assert_not_called()


@pytest.mark.django_db
def test_direct_relation_does_not_link_foreign_target_or_trust_source_id(monkeypatch):
    token_task = create_token_task(team=[1])
    foreign_task = create_token_task(team=[2])
    model_id = token_task.task.config["model_id"]
    target_model = unique_crval_name("target_model")
    foreign_target = {
        "_id": 2,
        "model_id": target_model,
        "organization": [2],
        "collect_task": f"cr_{foreign_task.task.id}",
    }
    owned_source = {
        "_id": 1,
        "model_id": model_id,
        "organization": [1],
        "collect_task": f"cr_{token_task.task.id}",
    }
    filters_seen = _record_instance_queries(
        monkeypatch,
        foreign_target,
        owned_source=owned_source,
    )
    graph_write = Mock()
    monkeypatch.setattr(relation_service, "_create_edge", graph_write)
    asst_id = unique_crval_name("association")
    monkeypatch.setattr(
        ModelManage,
        "model_association_info_search",
        lambda model_asst_id: {
            "model_asst_id": asst_id,
            "src_model_id": model_id,
            "dst_model_id": target_model,
            "asst_id": "connect",
            "mapping": "n:n",
        },
    )
    relation = {
        "source": {"_id": 1, "model_id": model_id},
        "target": {
            "model_id": target_model,
            "identity": {"inst_name": "target"},
        },
        "asst_id": asst_id,
    }

    result = relation_service.process(token_task.task, [relation], {}, "crval_validator")

    unscoped_filters = [
        [
            {"field": "model_id", "type": "str=", "value": target_model},
            {"field": "inst_name", "type": "str=", "value": "target"},
        ]
    ]
    current_bad = (
        filters_seen == unscoped_filters
        and graph_write.call_args_list
        == [call(1, foreign_target["_id"], asst_id, "crval_validator", ANY)]
        and result == {"pending": 0}
    )
    if current_bad:
        raise KnownProductDefect(
            "CRV-F09: direct source _id was forwarded without lookup and an " "unscoped target query created an edge to a foreign owner/team node"
        )

    queried_fields = {item["field"] for item in filters_seen[0]}
    assert {"collect_task", "organization"}.issubset(queried_fields)
    graph_write.assert_not_called()
    assert result == {"pending": 1}


@pytest.mark.django_db
def test_pending_backfill_does_not_link_foreign_target(monkeypatch):
    token_task = create_token_task(team=[1])
    foreign_task = create_token_task(team=[2])
    model_id = token_task.task.config["model_id"]
    target_model = unique_crval_name("target_model")
    asst_id = unique_crval_name("association")
    pending = CustomReportingPendingRelation.objects.create(
        task=token_task.task,
        source_model_id=model_id,
        target_model_id=target_model,
        relation_payload={
            "source": {"_id": 1, "model_id": model_id},
            "target": {
                "model_id": target_model,
                "identity": {"inst_name": "target"},
            },
            "asst_id": asst_id,
        },
    )
    foreign_target = {
        "_id": 2,
        "model_id": target_model,
        "organization": [2],
        "collect_task": f"cr_{foreign_task.task.id}",
    }
    owned_source = {
        "_id": 1,
        "model_id": model_id,
        "organization": [1],
        "collect_task": f"cr_{token_task.task.id}",
    }
    filters_seen = _record_instance_queries(
        monkeypatch,
        foreign_target,
        owned_source=owned_source,
    )
    graph_write = Mock()
    monkeypatch.setattr(relation_service, "_create_edge", graph_write)
    monkeypatch.setattr(
        ModelManage,
        "model_association_info_search",
        lambda model_asst_id: {
            "model_asst_id": asst_id,
            "src_model_id": model_id,
            "dst_model_id": target_model,
            "asst_id": "connect",
            "mapping": "n:n",
        },
    )

    resolved = relation_service.backfill(token_task.task, "crval_validator")

    unscoped_filters = [
        [
            {"field": "model_id", "type": "str=", "value": target_model},
            {"field": "inst_name", "type": "str=", "value": "target"},
        ]
    ]
    current_bad = (
        filters_seen == unscoped_filters
        and graph_write.call_args_list
        == [call(1, foreign_target["_id"], asst_id, "crval_validator", ANY)]
        and resolved == 1
        and not CustomReportingPendingRelation.objects.filter(id=pending.id).exists()
    )
    if current_bad:
        raise KnownProductDefect(
            "CRV-F09: pending backfill used an unscoped target query, created " "an edge to a foreign owner/team node, and deleted the pending record"
        )

    assert len(filters_seen) == 2
    assert all(
        {"collect_task", "organization"}
        <= {item["field"] for item in filters}
        for filters in filters_seen
    )
    graph_write.assert_not_called()
    assert resolved == 0
    assert CustomReportingPendingRelation.objects.filter(id=pending.id).exists()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "foreign_overrides",
    [
        {"model_id": "foreign_model"},
        {"collect_task": "cr_foreign_task"},
        {"organization": [2]},
    ],
)
def test_direct_relation_rejects_foreign_source_id(monkeypatch, foreign_overrides):
    token_task = create_token_task(team=[1])
    model_id = token_task.task.config["model_id"]
    target_model = unique_crval_name("target_model")
    asst_id = unique_crval_name("association")
    source = {
        "_id": 1,
        "model_id": model_id,
        "collect_task": f"cr_{token_task.task.id}",
        "organization": [1],
    }
    source.update(foreign_overrides)
    target = {
        "_id": 2,
        "model_id": target_model,
        "collect_task": f"cr_{token_task.task.id}",
        "organization": [1],
        "inst_name": "target",
    }
    filters_seen = []

    class RecordingGraph:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def query_entity(self, entity, filters, **kwargs):
            filters_seen.append(list(filters))
            fields = {item["field"] for item in filters}
            if "id" in fields:
                return [source], 1
            return [target], 1

    monkeypatch.setattr(instance_service, "GraphClient", RecordingGraph)
    monkeypatch.setattr(relation_service, "GraphClient", RecordingGraph, raising=False)
    graph_write = Mock()
    monkeypatch.setattr(relation_service, "_create_edge", graph_write)
    monkeypatch.setattr(
        ModelManage,
        "model_association_info_search",
        lambda model_asst_id: {
            "model_asst_id": asst_id,
            "src_model_id": model_id,
            "dst_model_id": target_model,
            "asst_id": "connect",
            "mapping": "n:n",
        },
    )
    relation = {
        "source": {"_id": 1, "model_id": model_id},
        "target": {"model_id": target_model, "identity": {"inst_name": "target"}},
        "asst_id": asst_id,
    }

    result = relation_service.process(token_task.task, [relation], {}, "crval_validator")

    assert filters_seen
    graph_write.assert_not_called()
    assert result == {"pending": 1}


@pytest.mark.django_db
def test_direct_relation_target_id_uses_owned_resolver(monkeypatch):
    token_task = create_token_task(team=[1])
    model_id = token_task.task.config["model_id"]
    target_model = unique_crval_name("target_model")
    asst_id = unique_crval_name("association")
    instances = {
        1: {
            "_id": 1,
            "model_id": model_id,
            "collect_task": f"cr_{token_task.task.id}",
            "organization": [1],
        },
        2: {
            "_id": 2,
            "model_id": target_model,
            "collect_task": f"cr_{token_task.task.id}",
            "organization": [1],
        },
    }
    filters_seen = []

    class RecordingGraph:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def query_entity(self, entity, filters, **kwargs):
            filters_seen.append(list(filters))
            id_filter = next(item for item in filters if item["field"] == "id")
            return [instances[id_filter["value"]]], 1

    monkeypatch.setattr(instance_service, "GraphClient", RecordingGraph)
    monkeypatch.setattr(relation_service, "GraphClient", RecordingGraph, raising=False)
    graph_write = Mock()
    monkeypatch.setattr(relation_service, "_create_edge", graph_write)
    monkeypatch.setattr(
        ModelManage,
        "model_association_info_search",
        lambda model_asst_id: {
            "model_asst_id": asst_id,
            "src_model_id": model_id,
            "dst_model_id": target_model,
            "asst_id": "connect",
            "mapping": "n:n",
        },
    )
    relation = {
        "source": {"_id": 1, "model_id": model_id},
        "target": {"_id": 2, "model_id": target_model},
        "asst_id": asst_id,
    }

    result = relation_service.process(token_task.task, [relation], {}, "crval_validator")

    assert len(filters_seen) == 2
    assert all(
        {"model_id", "collect_task", "organization", "id"}
        <= {item["field"] for item in filters}
        for filters in filters_seen
    )
    graph_write.assert_called_once_with(1, 2, asst_id, "crval_validator", ANY)
    assert result == {"pending": 0}


@pytest.mark.django_db
def test_review_approval_does_not_delete_without_durable_approved_state(monkeypatch):
    token_task = create_token_task(cleanup_strategy="snapshot")
    _allow_owned_cleanup(monkeypatch)
    batch = CustomReportingBatch.objects.create(
        task=token_task.task,
        status=CustomReportingBatch.STATUS_SUCCESS,
    )
    review = CustomReportingCleanupReview.objects.create(
        batch=batch,
        status=CustomReportingCleanupReview.STATUS_PENDING,
        review_payload={"delete_ids": [10, 11]},
    )
    deleted = []
    monkeypatch.setattr(
        cleanup_service,
        "_snapshot_instances",
        lambda ids: [{"_id": item, "model_id": token_task.task.config["model_id"]} for item in ids],
    )
    monkeypatch.setattr(
        cleanup_service,
        "_delete_instances",
        lambda ids, operator, *args, **kwargs: deleted.extend(ids),
    )
    original_save = CustomReportingCleanupReview.save
    failed_once = False

    def fail_approved_save(self, *args, **kwargs):
        nonlocal failed_once
        if self.id == review.id and self.status == self.STATUS_APPROVED and not failed_once:
            failed_once = True
            raise RuntimeError("injected review save failure")
        return original_save(self, *args, **kwargs)

    monkeypatch.setattr(CustomReportingCleanupReview, "save", fail_approved_save)

    with pytest.raises(RuntimeError, match="injected review save failure"):
        cleanup_service.approve(token_task.task.id, review.id, "crval_validator")

    review.refresh_from_db()
    _assert_contract_or_known_defect(
        actual=(deleted, review.status),
        expected=([10, 11], CustomReportingCleanupReview.STATUS_APPROVING),
        known_bad=([10, 11], CustomReportingCleanupReview.STATUS_PENDING),
        finding="CRV-F10",
    )


@pytest.mark.django_db
def test_cleanup_stops_before_graph_delete_when_audit_fact_lookup_fails(monkeypatch):
    deleted = []
    audit_write = Mock()

    class RecordingGraph:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def batch_delete_entity(self, entity, ids):
            deleted.append((entity, list(ids)))

    monkeypatch.setattr(
        instance_service.InstanceManage,
        "query_entity_by_ids",
        Mock(side_effect=RuntimeError("audit fact lookup unavailable")),
    )
    monkeypatch.setattr(cleanup_service, "GraphClient", RecordingGraph)
    monkeypatch.setattr(cleanup_service, "batch_create_change_record", audit_write)

    with pytest.raises(RuntimeError, match="audit fact lookup unavailable"):
        cleanup_service._delete_instances([10, 11], "crval_validator")

    assert deleted == []
    audit_write.assert_not_called()


@pytest.mark.django_db
def test_cleanup_successful_empty_fact_lookup_is_idempotent_noop(monkeypatch):
    graph_delete = Mock()
    audit_write = Mock()

    class RecordingGraph:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        batch_delete_entity = graph_delete

    monkeypatch.setattr(instance_service.InstanceManage, "query_entity_by_ids", Mock(return_value=[]))
    monkeypatch.setattr(cleanup_service, "GraphClient", RecordingGraph)
    monkeypatch.setattr(cleanup_service, "batch_create_change_record", audit_write)

    cleanup_service._delete_instances([10, 11], "crval_validator")

    graph_delete.assert_not_called()
    audit_write.assert_not_called()


@pytest.mark.django_db
@pytest.mark.parametrize("entrypoint", ["snapshot", "approve", "expire"])
def test_cleanup_entrypoints_fail_closed_when_delete_fact_lookup_fails(
    monkeypatch,
    entrypoint,
):
    token_task = create_token_task(cleanup_strategy="snapshot")
    batch = CustomReportingBatch.objects.create(
        task=token_task.task,
        status=CustomReportingBatch.STATUS_RUNNING,
    )
    graph_delete = Mock()
    audit_write = Mock()

    class RecordingGraph:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        batch_delete_entity = graph_delete

    _allow_owned_cleanup(monkeypatch)
    monkeypatch.setattr(
        instance_service.InstanceManage,
        "query_entity_by_ids",
        Mock(side_effect=RuntimeError("delete fact lookup unavailable")),
    )
    monkeypatch.setattr(cleanup_service, "GraphClient", RecordingGraph)
    monkeypatch.setattr(cleanup_service, "batch_create_change_record", audit_write)

    if entrypoint == "snapshot":
        def invoke():
            return cleanup_service.apply_snapshot(
                token_task.task,
                batch,
                old_ids=[10, 11],
                covered_ids=[10],
                operator="crval_validator",
            )

        review = None
    elif entrypoint == "approve":
        review = CustomReportingCleanupReview.objects.create(
            batch=batch,
            status=CustomReportingCleanupReview.STATUS_PENDING,
            review_payload={"delete_ids": [11]},
        )

        def invoke():
            return cleanup_service.approve(
                token_task.task.id,
                review.id,
                "crval_validator",
            )

    else:
        token_task.task.config.update(cleanup_strategy="expire", expire_days=1)
        token_task.task.save(update_fields=["config", "updated_at"], sync_scopes=False)
        stale_time = (timezone.now() - timezone.timedelta(days=2)).isoformat()

        class ExpireScope:
            def query(self, graph_client_cls):
                return [{"_id": 11, "cr_last_reported_at": stale_time}]

        monkeypatch.setattr(
            cleanup_service.ownership_service.OwnedInstanceScope,
            "from_task",
            lambda task, model_id: ExpireScope(),
        )

        def invoke():
            return cleanup_service.expire_cleanup(now=timezone.now())

        review = None

    with pytest.raises(RuntimeError, match="delete fact lookup unavailable"):
        invoke()

    graph_delete.assert_not_called()
    audit_write.assert_not_called()
    if review is not None:
        review.refresh_from_db()
        assert review.status == CustomReportingCleanupReview.STATUS_APPROVING


@pytest.mark.django_db
@pytest.mark.parametrize("entrypoint", ["snapshot", "approve", "expire"])
def test_cleanup_entrypoints_fail_closed_when_owner_fact_lookup_fails(
    monkeypatch,
    entrypoint,
):
    token_task = create_token_task(cleanup_strategy="snapshot")
    model_id = token_task.task.config["model_id"]
    batch = CustomReportingBatch.objects.create(
        task=token_task.task,
        status=CustomReportingBatch.STATUS_RUNNING,
    )
    delete_instances = Mock()
    audit_write = Mock()
    stale_time = (timezone.now() - timezone.timedelta(days=2)).isoformat()

    class OwnerLookupGraph:
        query_count = 0

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def query_entity(self, entity, filters, **kwargs):
            type(self).query_count += 1
            if entrypoint == "expire" and type(self).query_count == 1:
                return [
                    {
                        "_id": 11,
                        "model_id": model_id,
                        "collect_task": f"cr_{token_task.task.id}",
                        "organization": [1],
                        "cr_last_reported_at": stale_time,
                    }
                ], 1
            raise RuntimeError("owner fact lookup unavailable")

    monkeypatch.setattr(cleanup_service, "GraphClient", OwnerLookupGraph)
    monkeypatch.setattr(cleanup_service, "_delete_instances", delete_instances)
    monkeypatch.setattr(cleanup_service, "batch_create_change_record", audit_write)

    if entrypoint == "snapshot":
        def invoke():
            return cleanup_service.apply_snapshot(
                token_task.task,
                batch,
                old_ids=[11],
                covered_ids=[99],
                operator="crval_validator",
            )

        review = None
    elif entrypoint == "approve":
        review = CustomReportingCleanupReview.objects.create(
            batch=batch,
            status=CustomReportingCleanupReview.STATUS_PENDING,
            review_payload={"delete_ids": [11]},
        )

        def invoke():
            return cleanup_service.approve(
                token_task.task.id,
                review.id,
                "crval_validator",
            )

    else:
        token_task.task.config.update(cleanup_strategy="expire", expire_days=1)
        token_task.task.save(update_fields=["config", "updated_at"], sync_scopes=False)

        def invoke():
            return cleanup_service.expire_cleanup(now=timezone.now())

        review = None

    with pytest.raises(RuntimeError, match="owner fact lookup unavailable"):
        invoke()

    assert OwnerLookupGraph.query_count == (2 if entrypoint == "expire" else 1)
    delete_instances.assert_not_called()
    audit_write.assert_not_called()
    if review is not None:
        review.refresh_from_db()
        assert review.status == CustomReportingCleanupReview.STATUS_APPROVING


@pytest.mark.django_db
def test_concurrent_cleanup_approval_has_one_cas_winner_and_one_delete(monkeypatch):
    token_task = create_token_task(cleanup_strategy="snapshot")
    _allow_owned_cleanup(monkeypatch)
    batch = CustomReportingBatch.objects.create(
        task=token_task.task,
        status=CustomReportingBatch.STATUS_SUCCESS,
    )
    review = CustomReportingCleanupReview.objects.create(
        batch=batch,
        status=CustomReportingCleanupReview.STATUS_PENDING,
        review_payload={"delete_ids": [10]},
    )
    first_reader = CustomReportingCleanupReview.objects.get(id=review.id)
    second_reader = CustomReportingCleanupReview.objects.get(id=review.id)
    deleted = []
    monkeypatch.setattr(
        cleanup_service,
        "_snapshot_instances",
        lambda ids: [{"_id": item, "model_id": token_task.task.config["model_id"]} for item in ids],
    )
    monkeypatch.setattr(cleanup_service, "_delete_instances", lambda ids, operator, *args, **kwargs: deleted.extend(ids))
    monkeypatch.setattr(cleanup_service, "_get_review", Mock(side_effect=[first_reader, second_reader]))

    first = cleanup_service.approve(token_task.task.id, review.id, "reviewer_one")
    second_rejected = False
    try:
        cleanup_service.approve(token_task.task.id, review.id, "reviewer_two")
    except BaseAppException:
        second_rejected = True

    _assert_contract_or_known_defect(
        actual=(first["status"], second_rejected, deleted),
        expected=(CustomReportingCleanupReview.STATUS_APPROVED, True, [10]),
        known_bad=(CustomReportingCleanupReview.STATUS_APPROVED, False, [10, 10]),
        finding="CRV-F21",
    )


@pytest.mark.django_db
def test_review_approval_retry_advances_after_transient_db_failure(monkeypatch):
    token_task = create_token_task(cleanup_strategy="snapshot")
    _allow_owned_cleanup(monkeypatch)
    batch = CustomReportingBatch.objects.create(task=token_task.task, status=CustomReportingBatch.STATUS_SUCCESS)
    review = CustomReportingCleanupReview.objects.create(
        batch=batch,
        status=CustomReportingCleanupReview.STATUS_PENDING,
        review_payload={"delete_ids": [10, 11]},
    )
    deleted = []
    monkeypatch.setattr(
        cleanup_service,
        "_snapshot_instances",
        lambda ids: [{"_id": item, "model_id": token_task.task.config["model_id"]} for item in ids],
    )
    monkeypatch.setattr(cleanup_service, "_delete_instances", lambda ids, operator, *args, **kwargs: deleted.extend(ids))
    original_save = CustomReportingCleanupReview.save
    failed_once = False

    def fail_first_approved_save(self, *args, **kwargs):
        nonlocal failed_once
        if self.id == review.id and self.status == self.STATUS_APPROVED and not failed_once:
            failed_once = True
            raise RuntimeError("injected review save failure")
        return original_save(self, *args, **kwargs)

    monkeypatch.setattr(CustomReportingCleanupReview, "save", fail_first_approved_save)

    with pytest.raises(RuntimeError, match="injected review save failure"):
        cleanup_service.approve(token_task.task.id, review.id, "crval_validator")

    operation = CustomReportingOperation.objects.get(action="cleanup_review_approve")
    CustomReportingOperation.objects.filter(id=operation.id).update(lease_expires_at=timezone.now() - timezone.timedelta(seconds=1))
    result = reconcile_service.reconcile_operation(operation.operation_id)

    review.refresh_from_db()
    assert result == {"id": review.id, "status": review.STATUS_APPROVED}
    assert review.status == review.STATUS_APPROVED
    assert deleted == [10, 11]


@pytest.mark.django_db
def test_review_graph_failure_keeps_review_pending(monkeypatch):
    token_task = create_token_task(cleanup_strategy="snapshot")
    _allow_owned_cleanup(monkeypatch)
    batch = CustomReportingBatch.objects.create(
        task=token_task.task,
        status=CustomReportingBatch.STATUS_SUCCESS,
    )
    review = CustomReportingCleanupReview.objects.create(
        batch=batch,
        status=CustomReportingCleanupReview.STATUS_PENDING,
        review_payload={"delete_ids": [10]},
    )
    monkeypatch.setattr(
        cleanup_service,
        "_snapshot_instances",
        lambda ids: [{"_id": item, "model_id": token_task.task.config["model_id"]} for item in ids],
    )
    monkeypatch.setattr(
        cleanup_service,
        "_delete_instances",
        Mock(side_effect=RuntimeError("injected graph failure")),
    )

    with pytest.raises(RuntimeError, match="injected graph failure"):
        cleanup_service.approve(token_task.task.id, review.id, "crval_validator")

    review.refresh_from_db()
    assert review.status == CustomReportingCleanupReview.STATUS_APPROVING
    assert review.reviewed_at is None


@pytest.mark.django_db
def test_snapshot_threshold_and_none_strategy_keep_safe_positive_branches(monkeypatch):
    token_task = create_token_task(cleanup_strategy="snapshot")
    _allow_owned_cleanup(monkeypatch)
    token_task.task.config["snapshot_delete_ratio_threshold"] = 50
    token_task.task.save(update_fields=["config", "updated_at"], sync_scopes=False)
    equal_batch = CustomReportingBatch.objects.create(task=token_task.task)
    over_batch = CustomReportingBatch.objects.create(task=token_task.task)
    deleted = []
    monkeypatch.setattr(
        cleanup_service,
        "_snapshot_instances",
        lambda ids: [{"_id": item, "model_id": token_task.task.config["model_id"]} for item in ids],
    )
    monkeypatch.setattr(
        cleanup_service,
        "_delete_instances",
        lambda ids, operator, *args, **kwargs: deleted.append(list(ids)),
    )

    equal = cleanup_service.apply_snapshot(
        token_task.task,
        equal_batch,
        old_ids=[1, 2],
        covered_ids=[1],
        operator="crval_validator",
    )
    over = cleanup_service.apply_snapshot(
        token_task.task,
        over_batch,
        old_ids=[1, 2, 3],
        covered_ids=[1],
        operator="crval_validator",
    )

    assert equal == {"deleted": 1, "review_created": False}
    assert deleted == [[2]]
    assert over == {"deleted": 0, "review_created": True}
    assert CustomReportingCleanupReview.objects.filter(batch=over_batch).count() == 1

    none_task = create_token_task(cleanup_strategy="none")
    monkeypatch.setattr(
        ModelManage,
        "search_model_attr",
        lambda model_id: [{"attr_id": "inst_name", "attr_type": "str"}],
    )
    monkeypatch.setattr(
        ingest_service.merge_service,
        "merge_instances",
        lambda *args, **kwargs: _merge_result(old_data=[{"_id": 99}]),
    )
    monkeypatch.setattr(
        ingest_service.relation_service,
        "process",
        lambda *args: {"pending": 0},
    )
    snapshot = Mock()
    monkeypatch.setattr(cleanup_service, "apply_snapshot", snapshot)

    result = ingest_service.ingest(none_task.raw_token, {"instances": []})

    assert result["summary"]["errors"] == 0
    snapshot.assert_not_called()
