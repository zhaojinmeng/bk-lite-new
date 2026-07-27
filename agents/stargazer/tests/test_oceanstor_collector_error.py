from unittest.mock import Mock

import pytest


def test_OceanStor资源API错误不伪装为空集(monkeypatch):
    from plugins.inputs.oceanstor import oceanstor_info

    response = Mock()
    response.json.return_value = {
        "error": {
            "code": 1077948996,
            "description": "The user name or password is incorrect.",
        }
    }
    monkeypatch.setattr(oceanstor_info.requests, "get", Mock(return_value=response))
    manager = oceanstor_info.OceanStorManager(
        {
            "host": "oceanstor.example.invalid",
            "username": "contract-user",
            "password": "contract-password",
        }
    )
    manager.device_id = "device-contract"
    manager.token = "redacted"

    with pytest.raises(RuntimeError, match="1077948996"):
        manager._fetch_all("storagepool")
