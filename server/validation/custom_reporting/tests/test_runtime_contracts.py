from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import ANY, Mock, patch

import pytest

from apps.cmdb.services.model import ModelManage
from apps.cmdb.views.instance import InstanceViewSet
from apps.cmdb_enterprise.custom_reporting.models import CustomReportingBatch, CustomReportingPendingRelation, CustomReportingTask
from apps.cmdb_enterprise.custom_reporting.provider import CustomReportingProvider
from apps.cmdb_enterprise.custom_reporting.services import (
    credential_service,
    field_service,
    ingest_service,
    merge_service,
    model_service,
    relation_service,
)
from apps.core.exceptions.base_app_exception import BaseAppException
from validation.custom_reporting.tests.factories import create_token_task, unique_crval_name

TASKS_URL = "/api/v1/cmdb/api/custom_reporting/tasks/"
INGEST_URL = "/api/v1/cmdb/api/custom_reporting/ingest/"


class KnownProductDefect(AssertionError):
    pass


def _assert_contract_or_known_defect(*, actual, expected, known_bad, finding):
    if actual == known_bad:
        raise KnownProductDefect(f"{finding}: observed {known_bad!r}")
    assert actual == expected


def _payload(*, team=None):
    return {
        "name": unique_crval_name("task"),
        "team": [1] if team is None else list(team),
        "config": {
            "mode": "standard",
            "model_id": unique_crval_name("model"),
            "identity_keys": ["inst_name"],
        },
        "is_enabled": True,
    }


def _authorize(api_client, authenticated_user, *permissions):
    authenticated_user.permission = {"cmdb": set(permissions)}
    authenticated_user.is_superuser = False
    api_client.force_authenticate(authenticated_user)


def _patch_relation_association(
    monkeypatch,
    *,
    model_asst_id,
    source_model_id,
    target_model_id,
):
    monkeypatch.setattr(
        ModelManage,
        "model_association_info_search",
        lambda requested_id: {
            "model_asst_id": requested_id,
            "src_model_id": source_model_id,
            "dst_model_id": target_model_id,
            "asst_id": "connect",
            "mapping": "n:n",
        }
        if requested_id == model_asst_id
        else {},
    )


@pytest.fixture
def allowed_org_one(monkeypatch):
    monkeypatch.setattr(
        CustomReportingProvider,
        "_allowed_orgs",
        staticmethod(lambda request: [1]),
    )


@pytest.mark.django_db
def test_create_rejects_team_outside_requester_scope(
    api_client,
    authenticated_user,
    allowed_org_one,
):
    _authorize(api_client, authenticated_user, "model_management-Add Model")
    before = CustomReportingTask.objects.count()

    response = api_client.post(TASKS_URL, _payload(team=[2]), format="json")

    _assert_contract_or_known_defect(
        actual=(response.status_code, CustomReportingTask.objects.count()),
        expected=(403, before),
        known_bad=(200, before + 1),
        finding="CRV-F01",
    )


@pytest.mark.django_db
def test_update_rejects_moving_task_outside_requester_scope(
    api_client,
    authenticated_user,
    allowed_org_one,
):
    token_task = create_token_task(team=[1])
    _authorize(api_client, authenticated_user, "model_management-Edit Model")

    response = api_client.put(
        f"{TASKS_URL}{token_task.task.id}/",
        {"team": [2]},
        format="json",
    )

    token_task.task.refresh_from_db()
    _assert_contract_or_known_defect(
        actual=(response.status_code, token_task.task.team),
        expected=(403, [1]),
        known_bad=(200, [2]),
        finding="CRV-F01",
    )


@pytest.mark.django_db
def test_list_allows_model_management_view_permission(
    api_client,
    authenticated_user,
    allowed_org_one,
):
    create_token_task(team=[1])
    _authorize(api_client, authenticated_user, "model_management-View")

    response = api_client.get(TASKS_URL)

    assert response.status_code == 200


@pytest.mark.django_db
def test_list_rejects_user_without_model_management_view_permission(
    api_client,
    authenticated_user,
    allowed_org_one,
):
    create_token_task(team=[1])
    _authorize(api_client, authenticated_user)

    response = api_client.get(TASKS_URL)

    _assert_contract_or_known_defect(
        actual=response.status_code,
        expected=403,
        known_bad=200,
        finding="CRV-F02",
    )


@pytest.mark.django_db
def test_create_allows_model_management_add_permission(
    api_client,
    authenticated_user,
    allowed_org_one,
):
    _authorize(api_client, authenticated_user, "model_management-Add Model")
    payload = _payload(team=[1])

    response = api_client.post(TASKS_URL, payload, format="json")

    assert response.status_code == 200
    assert CustomReportingTask.objects.filter(name=payload["name"], team=[1]).exists()


@pytest.mark.django_db
def test_create_rejects_user_without_model_management_add_permission(
    api_client,
    authenticated_user,
    allowed_org_one,
):
    _authorize(api_client, authenticated_user, "model_management-View")
    payload = _payload(team=[1])

    response = api_client.post(TASKS_URL, payload, format="json")

    _assert_contract_or_known_defect(
        actual=(
            response.status_code,
            CustomReportingTask.objects.filter(name=payload["name"]).exists(),
        ),
        expected=(403, False),
        known_bad=(200, True),
        finding="CRV-F02",
    )


@pytest.mark.django_db
def test_update_allows_model_management_edit_permission(
    api_client,
    authenticated_user,
    allowed_org_one,
):
    token_task = create_token_task(team=[1])
    _authorize(api_client, authenticated_user, "model_management-Edit Model")
    new_name = unique_crval_name("updated_task")

    response = api_client.put(
        f"{TASKS_URL}{token_task.task.id}/",
        {"name": new_name},
        format="json",
    )

    assert response.status_code == 200
    token_task.task.refresh_from_db()
    assert token_task.task.name == new_name


@pytest.mark.django_db
def test_update_rejects_user_without_model_management_edit_permission(
    api_client,
    authenticated_user,
    allowed_org_one,
):
    token_task = create_token_task(team=[1])
    _authorize(api_client, authenticated_user, "model_management-View")
    original_name = token_task.task.name
    attempted_name = unique_crval_name("updated_task")

    response = api_client.put(
        f"{TASKS_URL}{token_task.task.id}/",
        {"name": attempted_name},
        format="json",
    )

    token_task.task.refresh_from_db()
    _assert_contract_or_known_defect(
        actual=(response.status_code, token_task.task.name),
        expected=(403, original_name),
        known_bad=(200, attempted_name),
        finding="CRV-F02",
    )


def _successful_merge(*args, **kwargs):
    return {
        "created": 0,
        "updated": 0,
        "deleted": 0,
        "errors": 0,
        "covered_ids": [],
        "old_data": [],
        "index": {},
    }


def _successful_relations(*args, **kwargs):
    return {"pending": 0}


def test_api_secret_integer_group_list_uses_cmdb_org_scope_contract(monkeypatch):
    authorized_groups = Mock(return_value=[7])
    monkeypatch.setattr(
        "apps.cmdb.views.instance.GroupUtils.get_user_authorized_child_groups",
        authorized_groups,
    )

    for request in (
        SimpleNamespace(
            user=SimpleNamespace(group_list=[{"id": 7}], is_superuser=False),
            COOKIES={"current_team": "7"},
        ),
        SimpleNamespace(
            user=SimpleNamespace(group_list=[7], is_superuser=False),
            COOKIES={},
            _api_current_team=7,
        ),
    ):
        actual = InstanceViewSet._get_allowed_org_ids(request)
        assert actual == [7]
        assert authorized_groups.call_args.kwargs["user_group_list"] == [7]


@pytest.mark.django_db
@pytest.mark.parametrize(
    "identity_keys",
    [[], [""], ["_id"]],
    ids=["empty", "blank-key", "reserved-key"],
)
def test_empty_or_invalid_identity_keys_rejected_before_graph_write(monkeypatch, identity_keys):
    token_task = create_token_task(identity_keys=identity_keys)
    add_write = Mock(return_value={"success": [], "failed": []})
    update_write = Mock(return_value={"success": [], "failed": []})

    monkeypatch.setattr(ModelManage, "search_model_attr", lambda model_id: [])
    monkeypatch.setattr(merge_service.Management, "add_inst", add_write)
    monkeypatch.setattr(merge_service.Management, "update_inst", update_write)

    class EmptyGraph:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def query_entity(self, *args, **kwargs):
            return [], 0

    monkeypatch.setattr(merge_service, "GraphClient", EmptyGraph)

    rejected = False
    try:
        merge_service.merge_instances(
            token_task.task,
            token_task.task.config["model_id"],
            [{"inst_name": "a"}, {"inst_name": "b"}],
            "crval_validator",
        )
    except BaseAppException as exc:
        assert "身份键" in str(exc)
        rejected = True

    _assert_contract_or_known_defect(
        actual=(rejected, add_write.call_count, update_write.call_count),
        expected=(True, 0, 0),
        known_bad=(False, 1, 1),
        finding="CRV-F03",
    )


@pytest.mark.django_db
@pytest.mark.parametrize("invalid_field", ["crval_unknown", "_id"])
def test_standard_schema_rejects_unknown_and_reserved_fields_before_merge(
    monkeypatch,
    invalid_field,
):
    token_task = create_token_task(mode="standard")
    merge = Mock(side_effect=_successful_merge)
    monkeypatch.setattr(
        ModelManage,
        "search_model_attr",
        lambda model_id: [{"attr_id": "inst_name", "attr_type": "str"}],
    )
    monkeypatch.setattr(ingest_service.merge_service, "merge_instances", merge)
    monkeypatch.setattr(ingest_service.relation_service, "process", _successful_relations)
    payload = {"instances": [{"inst_name": "a", invalid_field: "unsafe"}]}

    rejected = False
    try:
        ingest_service.ingest(token_task.raw_token, payload, operator="crval_validator")
    except BaseAppException:
        rejected = True

    _assert_contract_or_known_defect(
        actual=(
            rejected,
            CustomReportingBatch.objects.filter(task=token_task.task).count(),
            merge.call_args.args[2] if merge.called else None,
        ),
        expected=(True, 0, None),
        known_bad=(False, 1, payload["instances"]),
        finding="CRV-F04",
    )


@pytest.mark.django_db
def test_quick_mode_registers_new_business_field_before_merge(monkeypatch):
    token_task = create_token_task(mode="quick")
    events = []
    monkeypatch.setattr(
        ModelManage,
        "search_model_attr",
        lambda model_id: [{"attr_id": "inst_name", "attr_type": "str"}],
    )
    monkeypatch.setattr(model_service, "get_declared_attr_ids", lambda model_id: {"inst_name"})
    monkeypatch.setattr(
        ModelManage,
        "create_model_attr",
        lambda model_id, attr, username="admin": events.append(("register", model_id, attr["attr_id"], username)),
    )
    monkeypatch.setattr(field_service, "record_registrations", Mock())
    monkeypatch.setattr(
        ingest_service.merge_service,
        "merge_instances",
        lambda task, model_id, instances, operator, **kwargs: events.append(("merge", instances))
        or _successful_merge(),
    )
    monkeypatch.setattr(ingest_service.relation_service, "process", _successful_relations)
    instances = [{"inst_name": "a", "crval_owner": "ops"}]

    ingest_service.ingest(
        token_task.raw_token,
        {"instances": instances},
        operator="crval_validator",
    )

    assert events == [
        (
            "register",
            token_task.task.config["model_id"],
            "crval_owner",
            "crval_validator",
        ),
        ("merge", instances),
    ]


@pytest.mark.django_db
def test_quick_mode_reserved_id_field_is_not_registered_or_written(monkeypatch):
    token_task = create_token_task(mode="quick")
    created_attrs = []
    graph_add_payloads = []
    caller_timestamp = "caller-controlled"
    attrs = [{"attr_id": "inst_name", "attr_name": "名称", "attr_type": "str", "is_only": True}]
    monkeypatch.setattr(ModelManage, "search_model_attr", lambda model_id: attrs)
    monkeypatch.setattr(
        ModelManage,
        "create_model_attr",
        lambda model_id, attr, username="admin": created_attrs.append(attr["attr_id"]),
    )
    monkeypatch.setattr(field_service, "record_registrations", Mock())
    monkeypatch.setattr(ingest_service.relation_service, "process", _successful_relations)

    class EmptyGraph:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def query_entity(self, *args, **kwargs):
            return [], 0

    monkeypatch.setattr(merge_service, "GraphClient", EmptyGraph)

    def capture_add(instances):
        graph_add_payloads.extend(deepcopy(instances))
        return {"success": [], "failed": []}

    monkeypatch.setattr(merge_service.Management, "add_inst", Mock(side_effect=capture_add))
    monkeypatch.setattr(
        merge_service.Management,
        "update_inst",
        Mock(return_value={"success": [], "failed": []}),
    )
    payload_instances = [
        {
            "inst_name": "a",
            "crval_owner": "ops",
            "_id": 9001,
            "cr_last_reported_at": caller_timestamp,
        }
    ]

    ingest_service.ingest(
        token_task.raw_token,
        {"instances": payload_instances},
        operator="crval_validator",
    )

    assert len(graph_add_payloads) == 1
    written = graph_add_payloads[0]
    assert written["cr_last_reported_at"] != caller_timestamp
    _assert_contract_or_known_defect(
        actual=(created_attrs, written.get("_id")),
        expected=(["crval_owner"], None),
        known_bad=(["crval_owner"], 9001),
        finding="CRV-F05",
    )


@pytest.mark.django_db
def test_relation_endpoint_rejects_source_model_mismatch_without_side_effects(monkeypatch):
    token_task = create_token_task(mode="standard")
    target_model = unique_crval_name("target_model")
    wrong_source_model = unique_crval_name("wrong_source_model")
    graph_write = Mock()
    monkeypatch.setattr(
        ModelManage,
        "search_model_attr",
        lambda model_id: [{"attr_id": "inst_name", "attr_type": "str"}],
    )
    monkeypatch.setattr(ingest_service.merge_service, "merge_instances", _successful_merge)
    monkeypatch.setattr(relation_service, "_resolve_instance", lambda *args: {"_id": 2})
    monkeypatch.setattr(relation_service, "_create_edge", graph_write)
    association_id = unique_crval_name("association")
    relation = {
        "source": {
            "model_id": wrong_source_model,
            "identity": {"inst_name": "a"},
        },
        "target": {
            "model_id": target_model,
            "identity": {"inst_name": "b"},
        },
        "asst_id": association_id,
    }
    _patch_relation_association(
        monkeypatch,
        model_asst_id=association_id,
        source_model_id=token_task.task.config["model_id"],
        target_model_id=target_model,
    )

    rejected = False
    try:
        result = ingest_service.ingest(
            token_task.raw_token,
            {"instances": [], "relations": [relation]},
            operator="crval_validator",
        )
    except BaseAppException:
        rejected = True
        result = None

    credential = token_task.task.credentials.get()
    pending = CustomReportingPendingRelation.objects.filter(task=token_task.task).count()
    observed = (
        rejected,
        result["summary"]["pending_relations"] if result else None,
        pending,
        graph_write.call_count,
        CustomReportingBatch.objects.filter(task=token_task.task).count(),
        credential.last_used_at is not None,
    )
    _assert_contract_or_known_defect(
        actual=observed,
        expected=(True, None, 0, 0, 0, False),
        known_bad=(False, 1, 0, 1, 1, True),
        finding="CRV-F06",
    )


@pytest.mark.django_db
def test_factory_token_is_accepted_by_ingest_capability():
    token_task = create_token_task()

    with (
        patch.object(
            ModelManage,
            "search_model_attr",
            return_value=[{"attr_id": "inst_name", "attr_type": "str"}],
        ),
        patch.object(ingest_service.merge_service, "merge_instances", _successful_merge),
    ):
        result = ingest_service.ingest(
            token_task.raw_token,
            {"instances": [], "relations": []},
            operator="crval_runtime",
        )

    assert result["summary"]["errors"] == 0
    token_task.task.refresh_from_db()
    assert token_task.task.last_reported_at is not None


@pytest.mark.django_db
def test_rotating_factory_token_invalidates_old_and_accepts_new():
    token_task = create_token_task()
    credential = token_task.task.credentials.get()

    rotated = credential_service.rotate(token_task.task.id, credential.id)

    with pytest.raises(BaseAppException, match="无效|作废"):
        ingest_service.ingest(token_task.raw_token, {"instances": []})
    with (
        patch.object(
            ModelManage,
            "search_model_attr",
            return_value=[{"attr_id": "inst_name", "attr_type": "str"}],
        ),
        patch.object(ingest_service.merge_service, "merge_instances", _successful_merge),
    ):
        result = ingest_service.ingest(rotated["token"], {"instances": [], "relations": []})
    assert result["summary"]["errors"] == 0


@pytest.mark.django_db
def test_revoking_factory_token_blocks_ingest_capability():
    token_task = create_token_task()
    credential = token_task.task.credentials.get()

    credential_service.revoke(token_task.task.id, credential.id)

    with pytest.raises(BaseAppException, match="无效|作废"):
        ingest_service.ingest(token_task.raw_token, {"instances": []})


@pytest.mark.django_db
@pytest.mark.parametrize("token_state", ["missing", "random", "rotated", "revoked"])
def test_invalid_ingest_token_returns_authentication_error_instead_of_http_500(
    api_client,
    monkeypatch,
    token_state,
):
    token_task = create_token_task()
    credential = token_task.task.credentials.get()

    if token_state == "missing":
        submitted_token = None
    elif token_state == "random":
        submitted_token = unique_crval_name("invalid_token")
    elif token_state == "rotated":
        submitted_token = token_task.raw_token
        credential_service.rotate(token_task.task.id, credential.id)
    else:
        submitted_token = token_task.raw_token
        credential_service.revoke(token_task.task.id, credential.id)

    original_resolve = ingest_service._resolve_credential
    resolve_calls = []
    register_fields = Mock()
    merge_instances = Mock()
    monkeypatch.setattr(ingest_service.model_service, "register_model_fields", register_fields)
    monkeypatch.setattr(merge_service, "merge_instances", merge_instances)

    def recording_resolve(token):
        resolve_calls.append(token)
        return original_resolve(token)

    monkeypatch.setattr(ingest_service, "_resolve_credential", recording_resolve)
    if submitted_token is not None:
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {submitted_token}")

    response = api_client.post(
        INGEST_URL,
        {"instances": [], "relations": []},
        format="json",
        HTTP_IDEMPOTENCY_KEY=f"invalid-token-{token_state}",
    )

    exact_known_bad = (
        response.status_code == 500
        and resolve_calls == [submitted_token]
        and not CustomReportingBatch.objects.filter(task=token_task.task).exists()
        and not register_fields.called
        and not merge_instances.called
    )
    if exact_known_bad:
        raise KnownProductDefect(f"CRV-F16: {token_state} ingest token was mapped to HTTP 500")
    assert resolve_calls == [submitted_token]
    assert not CustomReportingBatch.objects.filter(task=token_task.task).exists()
    register_fields.assert_not_called()
    merge_instances.assert_not_called()
    assert response.status_code == 401
    if submitted_token is not None:
        assert submitted_token not in response.content.decode("utf-8")


@pytest.mark.django_db
@pytest.mark.parametrize(
    "source_id",
    [0, True, -1],
    ids=["zero", "bool", "negative"],
)
def test_relation_process_accepts_zero_id_but_rejects_bool_and_negative_ids(
    monkeypatch,
    source_id,
):
    token_task = create_token_task()
    graph_write = Mock()
    monkeypatch.setattr(relation_service, "_create_edge", graph_write)
    monkeypatch.setattr(
        relation_service,
        "_resolve_instance",
        lambda task, ref: {"_id": ref.instance_id if ref.instance_id is not None else 2},
    )
    association_id = unique_crval_name("association")
    relation = {
        "source": {"_id": source_id, "model_id": token_task.task.config["model_id"]},
        "target": {"model_id": "target", "identity": {"inst_name": "target"}},
        "asst_id": association_id,
    }
    _patch_relation_association(
        monkeypatch,
        model_asst_id=association_id,
        source_model_id=token_task.task.config["model_id"],
        target_model_id="target",
    )

    rejected = False
    try:
        result = relation_service.process(token_task.task, [relation], {}, "crval_validator")
    except BaseAppException:
        rejected = True
        result = None

    if source_id == 0:
        observed_bad = result == {"pending": 1} and graph_write.call_count == 0
        if observed_bad:
            raise KnownProductDefect("CRV-F24: process treated legitimate FalkorDB ID 0 as missing")
        assert result == {"pending": 0}
        graph_write.assert_called_once_with(
            0,
            2,
            relation["asst_id"],
            "crval_validator",
            ANY,
        )
    else:
        observed_bad = not rejected and graph_write.call_count == 1
        if observed_bad:
            raise KnownProductDefect(f"CRV-F24: process accepted invalid source ID {source_id!r}")
        assert rejected is True
        graph_write.assert_not_called()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "source_id",
    [0, True, -1],
    ids=["zero", "bool", "negative"],
)
def test_relation_backfill_accepts_zero_id_but_rejects_bool_and_negative_ids(
    monkeypatch,
    source_id,
):
    token_task = create_token_task()
    graph_write = Mock()
    association_id = unique_crval_name("association")
    pending = CustomReportingPendingRelation.objects.create(
        task=token_task.task,
        source_model_id=token_task.task.config["model_id"],
        target_model_id="target",
        relation_payload={
            "source": {"_id": source_id, "model_id": token_task.task.config["model_id"]},
            "target": {"model_id": "target", "identity": {"inst_name": "target"}},
            "asst_id": association_id,
        },
    )
    monkeypatch.setattr(relation_service, "_create_edge", graph_write)
    monkeypatch.setattr(
        relation_service,
        "_resolve_instance",
        lambda task, ref: {"_id": ref.instance_id if ref.instance_id is not None else 2},
    )
    _patch_relation_association(
        monkeypatch,
        model_asst_id=association_id,
        source_model_id=token_task.task.config["model_id"],
        target_model_id="target",
    )

    resolved = relation_service.backfill(token_task.task, "crval_validator")

    if source_id == 0:
        observed_bad = (
            resolved == 0
            and graph_write.call_count == 0
            and CustomReportingPendingRelation.objects.filter(id=pending.id).exists()
        )
        if observed_bad:
            raise KnownProductDefect("CRV-F24: backfill treated legitimate FalkorDB ID 0 as missing")
        assert resolved == 1
        graph_write.assert_called_once_with(
            0,
            2,
            pending.relation_payload["asst_id"],
            "crval_validator",
            ANY,
        )
        assert not CustomReportingPendingRelation.objects.filter(id=pending.id).exists()
    else:
        observed_bad = resolved == 1 and graph_write.call_count == 1
        if observed_bad:
            raise KnownProductDefect(f"CRV-F24: backfill accepted invalid source ID {source_id!r}")
        assert resolved == 0
        graph_write.assert_not_called()
        assert CustomReportingPendingRelation.objects.filter(id=pending.id).exists()
        delivery = relation_service.PendingRelationDelivery.objects.get(pending_relation=pending)
        assert delivery.state == "dead_letter"
        assert "GraphId" in delivery.last_error


@pytest.mark.django_db
@pytest.mark.parametrize("target_id", [True, -1], ids=["bool", "negative"])
def test_relation_process_rejects_invalid_resolved_target_graph_id(
    monkeypatch,
    target_id,
):
    token_task = create_token_task()
    association_id = unique_crval_name("association")
    graph_write = Mock()
    monkeypatch.setattr(relation_service, "_create_edge", graph_write)
    monkeypatch.setattr(
        relation_service,
        "_resolve_instance",
        lambda model_id, identity: {"_id": target_id},
    )
    _patch_relation_association(
        monkeypatch,
        model_asst_id=association_id,
        source_model_id=token_task.task.config["model_id"],
        target_model_id="target",
    )
    relation = {
        "source": {"_id": 1, "model_id": token_task.task.config["model_id"]},
        "target": {"model_id": "target", "identity": {"inst_name": "target"}},
        "asst_id": association_id,
    }

    with pytest.raises(BaseAppException, match="GraphId"):
        relation_service.process(
            token_task.task,
            [relation],
            {},
            "crval_validator",
        )

    graph_write.assert_not_called()


@pytest.mark.django_db
@pytest.mark.parametrize("target_id", [True, -1], ids=["bool", "negative"])
def test_relation_backfill_rejects_invalid_resolved_target_graph_id(
    monkeypatch,
    target_id,
):
    token_task = create_token_task()
    association_id = unique_crval_name("association")
    pending = CustomReportingPendingRelation.objects.create(
        task=token_task.task,
        source_model_id=token_task.task.config["model_id"],
        target_model_id="target",
        relation_payload={
            "source": {"_id": 1, "model_id": token_task.task.config["model_id"]},
            "target": {"model_id": "target", "identity": {"inst_name": "target"}},
            "asst_id": association_id,
        },
    )
    graph_write = Mock()
    monkeypatch.setattr(relation_service, "_create_edge", graph_write)
    monkeypatch.setattr(
        relation_service,
        "_resolve_instance",
        lambda model_id, identity: {"_id": target_id},
    )
    _patch_relation_association(
        monkeypatch,
        model_asst_id=association_id,
        source_model_id=token_task.task.config["model_id"],
        target_model_id="target",
    )

    assert relation_service.backfill(token_task.task, "crval_validator") == 0

    graph_write.assert_not_called()
    assert CustomReportingPendingRelation.objects.filter(id=pending.id).exists()
    delivery = relation_service.PendingRelationDelivery.objects.get(pending_relation=pending)
    assert delivery.state == "dead_letter"
    assert "GraphId" in delivery.last_error


@pytest.mark.django_db
def test_factories_preserve_explicit_empty_team():
    token_task = create_token_task(team=[])

    assert token_task.task.team == []
    assert _payload(team=[])["team"] == []


def test_known_product_defect_classifier_and_markers_are_precise():
    with pytest.raises(KnownProductDefect, match="CRV-F99"):
        _assert_contract_or_known_defect(
            actual=(200, True),
            expected=(403, False),
            known_bad=(200, True),
            finding="CRV-F99",
        )

    _assert_contract_or_known_defect(
        actual=(403, False),
        expected=(403, False),
        known_bad=(200, True),
        finding="CRV-F99",
    )

    with pytest.raises(AssertionError) as unexpected:
        _assert_contract_or_known_defect(
            actual=(500, False),
            expected=(403, False),
            known_bad=(200, True),
            finding="CRV-F99",
        )
    assert type(unexpected.value) is AssertionError
