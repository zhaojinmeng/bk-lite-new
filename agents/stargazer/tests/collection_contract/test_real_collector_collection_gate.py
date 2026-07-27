from types import SimpleNamespace

import pytest

import conftest


class _CollectedItem:
    def __init__(self, case_id):
        self.nodeid = f"test_real_collector_execution.py::{case_id}"
        self._marker = SimpleNamespace(args=(case_id,))

    def iter_markers(self, name):
        return (self._marker,) if name == "real_collector_binding" else ()


def test_删除任一真实collector用例会在collection阶段失败():
    case_ids = [binding.case_id for binding in conftest.PRODUCTION_ADAPTER_BINDINGS]
    items = [_CollectedItem(case_id) for case_id in case_ids[1:]]
    config = SimpleNamespace(args=[str(conftest.REAL_COLLECTOR_EXECUTION_PATH)])

    with pytest.raises(pytest.UsageError, match=case_ids[0]):
        conftest.pytest_collection_modifyitems(None, config, items)
