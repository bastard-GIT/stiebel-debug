"""Tests fuer gui/mqtt_secrets.py (ohne Qt)."""
import sys
from pathlib import Path

GUI_DIR = Path(__file__).resolve().parents[1] / "gui"
if str(GUI_DIR) not in sys.path:
    sys.path.insert(0, str(GUI_DIR))

import mqtt_secrets as ms  # noqa: E402


def test_load_settings(tmp_path):
    f = tmp_path / "secrets.yaml"
    f.write_text('# c\nmqtt_server: "10.0.0.1"\nmqtt_port: "1883"\nmqtt_username: u\nmqtt_password: "p w"\n', encoding="utf-8")
    assert ms.load_mqtt_settings(f) == ("10.0.0.1", 1883, "u", "p w")


def test_missing_file_or_key_returns_none(tmp_path):
    assert ms.load_mqtt_settings(tmp_path / "gibtsnicht.yaml") is None
    f = tmp_path / "s.yaml"
    f.write_text("mqtt_server: x\n", encoding="utf-8")
    assert ms.load_mqtt_settings(f) is None


def test_bad_port_returns_none(tmp_path):
    f = tmp_path / "s.yaml"
    f.write_text("mqtt_server: x\nmqtt_port: abc\nmqtt_username: u\nmqtt_password: p\n", encoding="utf-8")
    assert ms.load_mqtt_settings(f) is None


def test_default_path_is_mk1_secrets():
    import os
    assert ms.SECRETS_PATH.name == "secrets.yaml"
    if not os.environ.get("STIEBEL_SECRETS"):
        assert ms.SECRETS_PATH.parent.name == "mk1-debug"
