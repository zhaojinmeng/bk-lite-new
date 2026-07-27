from types import SimpleNamespace

import pytest

import conftest


class _CollectedItem:
    def __init__(self, case_id):
        self.nodeid = f"test_real_collector_execution.py::{case_id}"
        self._marker = SimpleNamespace(args=(case_id,))

    def iter_markers(self, name):
        return (self._marker,) if name == "real_collector_binding" else ()

    def get_closest_marker(self, name):
        return self._marker if name == "real_collector_binding" else None


def test_删除任一真实collector用例会在collection阶段失败():
    case_ids = [binding.case_id for binding in conftest.PRODUCTION_ADAPTER_BINDINGS]
    items = [_CollectedItem(case_id) for case_id in case_ids[1:]]
    config = SimpleNamespace(args=[str(conftest.REAL_COLLECTOR_EXECUTION_PATH)])
    session = SimpleNamespace(config=config, items=items, shouldfail=False)

    conftest.pytest_collection_finish(session)

    assert case_ids[0] in session.shouldfail


def test_marked空用例在teardown阶段失败():
    request = SimpleNamespace(node=_CollectedItem("cloud-qcloud"))
    guard = conftest._guard_real_collector_execution.__wrapped__(request)

    next(guard)

    with pytest.raises(AssertionError, match="cloud-qcloud.*未确认"):
        next(guard)


def test_真实发布确认必须匹配当前用例binding():
    request = SimpleNamespace(node=_CollectedItem("cloud-qcloud"))
    guard = conftest._guard_real_collector_execution.__wrapped__(request)
    next(guard)

    with pytest.raises(AssertionError, match="cloud-qcloud.*cloud-hwcloud"):
        conftest.confirm_real_collector_execution("cloud-hwcloud")

    conftest.confirm_real_collector_execution("cloud-qcloud")
    with pytest.raises(StopIteration):
        next(guard)
