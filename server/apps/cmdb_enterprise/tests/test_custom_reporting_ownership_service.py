from types import SimpleNamespace

import pytest

from apps.cmdb_enterprise.custom_reporting.services.ownership_service import (
    OwnedInstanceRef,
    resolve_owned_instance,
)
from apps.core.exceptions.base_app_exception import BaseAppException


def _graph_returning(instances):
    class FakeGraph:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def query_entity(self, entity, filters, **kwargs):
            return list(instances), len(instances)

    return FakeGraph


@pytest.fixture
def task():
    return SimpleNamespace(id=7, team=[1])


def _owned(**overrides):
    instance = {
        "_id": 11,
        "model_id": "host",
        "collect_task": "cr_7",
        "organization": [1],
        "inst_name": "owned",
    }
    instance.update(overrides)
    return instance


def test_resolver_selects_unique_owned_fact_after_foreign_first(task):
    foreign = _owned(_id=10, collect_task="cr_foreign", inst_name="foreign")
    owned = _owned()

    resolved = resolve_owned_instance(
        task,
        OwnedInstanceRef(model_id="host", identity=(("inst_name", "owned"),)),
        graph_client_cls=_graph_returning([foreign, owned]),
    )

    assert resolved == owned


@pytest.mark.parametrize("missing_field", ["model_id", "collect_task", "organization"])
def test_resolver_fails_closed_when_owner_fact_is_incomplete(task, missing_field):
    incomplete = _owned()
    incomplete.pop(missing_field)

    resolved = resolve_owned_instance(
        task,
        OwnedInstanceRef(model_id="host", identity=(("inst_name", "owned"),)),
        graph_client_cls=_graph_returning([incomplete]),
    )

    assert resolved is None


@pytest.mark.parametrize(
    ("ref", "instances"),
    [
        (
            OwnedInstanceRef(model_id="host", identity=(("inst_name", "owned"),)),
            [_owned(_id=11), _owned(_id=12)],
        ),
        (
            OwnedInstanceRef(model_id="host", instance_id=11),
            [_owned(_id=11), _owned(_id=11, inst_name="duplicate")],
        ),
    ],
    ids=["identity", "id"],
)
def test_resolver_rejects_multiple_owned_facts_with_generic_error(task, ref, instances):
    with pytest.raises(BaseAppException, match="实例查询结果不唯一"):
        resolve_owned_instance(
            task,
            ref,
            graph_client_cls=_graph_returning(instances),
        )


def test_resolver_returns_none_when_query_is_empty(task):
    resolved = resolve_owned_instance(
        task,
        OwnedInstanceRef(model_id="host", identity=(("inst_name", "missing"),)),
        graph_client_cls=_graph_returning([]),
    )

    assert resolved is None
