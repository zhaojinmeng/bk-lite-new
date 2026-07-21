import importlib

import yaml


def test_每个生产binding可解析真实默认collector入口(production_adapter_binding):
    binding = production_adapter_binding
    assert (
        binding.plugin_path.is_file()
    ), f"{binding.case_id}: 缺少生产插件 {binding.plugin_path}"
    plugin_config = yaml.safe_load(binding.plugin_path.read_text(encoding="utf-8"))
    default_executor = plugin_config["default_executor"]
    collector = plugin_config["executors"][default_executor]["collector"]

    module = importlib.import_module(collector["module"])
    collector_class = getattr(module, collector["class"])

    assert collector_class.__name__ == collector["class"]
    assert collector_class.__module__ == collector["module"]
