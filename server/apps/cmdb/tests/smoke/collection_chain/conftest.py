from __future__ import annotations

import os


def pytest_collection_modifyitems(config, items) -> None:
    """默认明确 deselect 真实 Docker smoke，避免用 skip 冒充验证。"""
    if os.getenv("CMDB_COLLECTION_SMOKE") == "1":
        return
    selected = []
    deselected = []
    for item in items:
        if item.get_closest_marker("real_smoke"):
            deselected.append(item)
        else:
            selected.append(item)
    if deselected:
        config.hook.pytest_deselected(items=deselected)
        items[:] = selected
