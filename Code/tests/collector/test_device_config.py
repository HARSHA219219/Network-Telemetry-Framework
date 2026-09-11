from __future__ import annotations

from pathlib import Path

from collector.config.device_config import load_devices


def test_load_devices_parses_authorized_and_defaults(tmp_path: Path) -> None:
    yaml_content = """
devices:
  - device_id: router-01
    ip: 10.0.0.1
    authorized: true
    kind: router
  - device_id: switch-01
    ip: 10.0.0.3
    kind: switch
"""
    config_file = tmp_path / "devices.yaml"
    config_file.write_text(yaml_content)

    devices = load_devices(str(config_file))

    assert len(devices) == 2

    router = devices[0]
    assert router.device_id == "router-01"
    assert router.ip == "10.0.0.1"
    assert router.authorized is True
    assert router.port == 161  # default

    switch = devices[1]
    assert switch.device_id == "switch-01"
    assert switch.authorized is False  # not specified -> defaults to False, safe by default


def test_load_devices_from_repo_config_file() -> None:
    """Sanity check against the real config/devices.yaml shipped in the repo."""
    devices = load_devices("config/devices.yaml")

    assert len(devices) == 4
    assert all(d.authorized is False for d in devices), (
        "Shipped devices.yaml must default to unauthorized until a real, "
        "permitted device is explicitly enabled."
    )
