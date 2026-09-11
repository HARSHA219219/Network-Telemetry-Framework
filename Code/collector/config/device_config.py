"""
Loads config/devices.yaml into DeviceConfig objects.

Deliberately does NOT read SNMP credentials here - those come from
environment variables at poll time (collector/snmp/credentials.py), so
they never pass through this YAML-parsing code path.
"""

from __future__ import annotations

import yaml

from collector.sources.base import DeviceConfig


def load_devices(path: str) -> list[DeviceConfig]:
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    devices: list[DeviceConfig] = []
    for entry in raw.get("devices", []):
        devices.append(
            DeviceConfig(
                device_id=entry["device_id"],
                ip=entry["ip"],
                authorized=bool(entry.get("authorized", False)),
                port=int(entry.get("port", 161)),
                extra={"kind": entry.get("kind")},
            )
        )
    return devices
