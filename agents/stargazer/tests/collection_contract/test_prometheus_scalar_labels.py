from plugins.base_utils import convert_to_prometheus_format
from semantics import parse_prometheus


def test_合法标量假值不会被静默丢弃():
    text = convert_to_prometheus_format(
        {
            "host": [
                {
                    "model_id": "host",
                    "zero": 0,
                    "disabled": False,
                    "empty": "",
                    "missing": None,
                }
            ]
        }
    )

    labels = dict(next(iter(parse_prometheus(text))).labels)

    assert labels["zero"] == "0"
    assert labels["disabled"] == "False"
    assert labels["empty"] == ""
    assert "missing" not in labels
