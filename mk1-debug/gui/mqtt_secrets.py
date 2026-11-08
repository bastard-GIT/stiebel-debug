"""MQTT-Zugangsdaten aus der secrets.yaml des Mk1-Projekts (kein Qt, kein paho).

Quelle: mk1-debug/secrets.yaml oder $STIEBEL_SECRETS (Schluessel mqtt_server, mqtt_port, mqtt_username,
mqtt_password). Werte werden nie geloggt oder ausgegeben.
"""
from __future__ import annotations

import os
from pathlib import Path

# Umgebungsvariable STIEBEL_SECRETS ueberschreibt, Standard: mk1-debug/secrets.yaml (nicht im Repo)
SECRETS_PATH = Path(os.environ.get("STIEBEL_SECRETS") or Path(__file__).resolve().parents[1] / "secrets.yaml")
SECRET_KEYS = ("mqtt_server", "mqtt_port", "mqtt_username", "mqtt_password")


def parse_secrets(text: str) -> dict[str, str]:
    """Einfacher Zeilen-Parser fuer `key: "value"` und `key: value`. Leere Zeilen und # ignorieren."""
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            out[key] = value
    return out


def load_mqtt_settings(path: str | Path | None = None) -> tuple[str, int, str, str] | None:
    """(host, port, user, password) aus der secrets.yaml, None wenn Datei/Schluessel fehlen."""
    try:
        text = Path(path or SECRETS_PATH).read_text(encoding="utf-8")
    except OSError:
        return None
    values = parse_secrets(text)
    if any(k not in values for k in SECRET_KEYS):
        return None
    try:
        port = int(values["mqtt_port"])
    except ValueError:
        return None
    return values["mqtt_server"], port, values["mqtt_username"], values["mqtt_password"]
