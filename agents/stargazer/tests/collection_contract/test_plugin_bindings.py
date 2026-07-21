import ast
import importlib
import importlib.util

import yaml


def test_每个生产binding可解析真实默认collector入口(production_adapter_binding):
    binding = production_adapter_binding
    assert (
        binding.plugin_path.is_file()
    ), f"{binding.case_id}: 缺少生产插件 {binding.plugin_path}"
    plugin_config = yaml.safe_load(binding.plugin_path.read_text(encoding="utf-8"))
    default_executor = plugin_config["default_executor"]
    collector = plugin_config["executors"][default_executor]["collector"]

    if not binding.collector_import_exemption_reason:
        module = importlib.import_module(collector["module"])
        collector_class = getattr(module, collector["class"])

        assert collector_class.__name__ == collector["class"]
        assert collector_class.__module__ == collector["module"]
        return

    spec = importlib.util.find_spec(collector["module"])
    assert spec is not None and spec.origin, binding.collector_import_exemption_reason
    source_path = binding.plugin_path.parent.joinpath(spec.origin).resolve()
    assert source_path.is_file(), binding.collector_import_exemption_reason
    source_tree = ast.parse(source_path.read_text(encoding="utf-8"))
    matching_classes = [
        node
        for node in source_tree.body
        if isinstance(node, ast.ClassDef) and node.name == collector["class"]
    ]
    assert len(matching_classes) == 1, binding.collector_import_exemption_reason
    assert any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        for node in matching_classes[0].body
    ), binding.collector_import_exemption_reason
