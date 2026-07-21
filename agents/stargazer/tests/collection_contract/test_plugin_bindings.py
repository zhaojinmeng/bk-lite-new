import importlib
from pathlib import Path

import yaml


def test_ip生产插件绑定真实scanner():
    plugin_path = (
        Path(__file__)
        .resolve()
        .parents[2]
        .joinpath("plugins", "inputs", "ip", "plugin.yml",)
    )
    plugin_config = yaml.safe_load(plugin_path.read_text(encoding="utf-8"))
    collector = plugin_config["executors"]["protocol"]["collector"]

    module = importlib.import_module(collector["module"])
    assert getattr(module, collector["class"]).__name__ == "IPDiscoveryScanner"
