from __future__ import annotations

import pytest

from collector.snmp.credentials import SNMPVersion, load_snmp_credentials
from collector.snmp.exceptions import SNMPConfigError


def test_loads_v2c_community_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SNMP_VERSION", "v2c")
    monkeypatch.setenv("SNMP_COMMUNITY_ROUTER_01", "public")

    creds = load_snmp_credentials("router-01")

    assert creds.version is SNMPVersion.V2C
    assert creds.community == "public"


def test_defaults_to_v2c_when_version_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SNMP_VERSION", raising=False)
    monkeypatch.setenv("SNMP_COMMUNITY_SWITCH_01", "public")

    creds = load_snmp_credentials("switch-01")

    assert creds.version is SNMPVersion.V2C


def test_missing_community_raises_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SNMP_VERSION", "v2c")
    monkeypatch.delenv("SNMP_COMMUNITY_ROUTER_02", raising=False)

    with pytest.raises(SNMPConfigError, match="SNMP_COMMUNITY_ROUTER_02"):
        load_snmp_credentials("router-02")


def test_v3_raises_not_implemented_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SNMP_VERSION", "v3")

    with pytest.raises(NotImplementedError, match="SNMPv3"):
        load_snmp_credentials("router-01")


def test_unsupported_version_raises_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SNMP_VERSION", "v1")

    with pytest.raises(SNMPConfigError, match="Unsupported SNMP_VERSION"):
        load_snmp_credentials("router-01")
