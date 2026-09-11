"""
Tests SNMPSource end-to-end logic (discovery + per-interface polling +
error handling) against a FakeSnmpTransport - a plain Python dict-backed
stand-in for a real device. No pysnmp, no sockets, no live device
required. This is exactly the point of the transport abstraction in
collector/snmp/transport.py.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from collector.snmp.credentials import SNMPCredentials, SNMPVersion
from collector.snmp.exceptions import SnmpTimeoutError
from collector.snmp.oids import (
    OID_IF_HC_IN_OCTETS,
    OID_IF_HC_OUT_OCTETS,
    OID_IF_HIGH_SPEED,
    OID_IF_IN_ERRORS,
    OID_IF_IN_UCAST_PKTS,
    OID_IF_NAME,
    OID_IF_OPER_STATUS,
    OID_IF_OUT_ERRORS,
    OID_IF_OUT_UCAST_PKTS,
    OID_SYS_UPTIME,
    indexed_oid,
)
from collector.snmp.snmp_source import SNMPSource
from collector.snmp.transport import SnmpTransport
from collector.sources.base import DeviceConfig


class FakeSnmpTransport(SnmpTransport):
    """In-memory stand-in for a real device's SNMP agent.

    `scalars` maps OID -> value for GET.
    `table_rows` maps a table-column base OID -> list of (full_oid, value)
    for WALK, simulating what a real device would return for that column.
    `unreachable` simulates a device that never answers at all.
    """

    def __init__(
        self,
        ip: str,
        port: int,
        credentials: SNMPCredentials,
        timeout_seconds: float,
        retries: int,
        scalars: dict[str, Any] | None = None,
        table_rows: dict[str, list[tuple[str, Any]]] | None = None,
        unreachable: bool = False,
    ) -> None:
        super().__init__(ip, port, credentials, timeout_seconds, retries)
        self.scalars = scalars or {}
        self.table_rows = table_rows or {}
        self.unreachable = unreachable
        self.closed = False

    async def get(self, oid: str) -> Any:
        if self.unreachable:
            raise SnmpTimeoutError(f"{self.ip}:{self.port} GET {oid} timed out (simulated)")
        if oid not in self.scalars:
            raise SnmpTimeoutError(f"{self.ip}:{self.port} GET {oid} timed out (no such OID in fixture)")
        return self.scalars[oid]

    async def walk(self, oid_prefix: str) -> list[tuple[str, Any]]:
        if self.unreachable:
            raise SnmpTimeoutError(f"{self.ip}:{self.port} WALK {oid_prefix} timed out (simulated)")
        return self.table_rows.get(oid_prefix, [])

    async def close(self) -> None:
        self.closed = True


def _make_two_interface_fixture() -> tuple[dict[str, Any], dict[str, list[tuple[str, Any]]]]:
    """Builds scalar/table fixtures representing a device with 2 interfaces."""
    scalars = {
        OID_SYS_UPTIME: 123456,  # TimeTicks -> 1234 seconds
        indexed_oid(OID_IF_OPER_STATUS, 1): 1,     # up
        indexed_oid(OID_IF_HIGH_SPEED, 1): 1000,     # 1000 Mbps = 1 Gbps
        indexed_oid(OID_IF_HC_IN_OCTETS, 1): 5_000_000,
        indexed_oid(OID_IF_HC_OUT_OCTETS, 1): 3_000_000,
        indexed_oid(OID_IF_IN_UCAST_PKTS, 1): 4000,
        indexed_oid(OID_IF_OUT_UCAST_PKTS, 1): 3500,
        indexed_oid(OID_IF_IN_ERRORS, 1): 0,
        indexed_oid(OID_IF_OUT_ERRORS, 1): 0,
        indexed_oid(OID_IF_OPER_STATUS, 2): 2,     # down
        indexed_oid(OID_IF_HIGH_SPEED, 2): 100,
        indexed_oid(OID_IF_HC_IN_OCTETS, 2): 0,
        indexed_oid(OID_IF_HC_OUT_OCTETS, 2): 0,
        indexed_oid(OID_IF_IN_UCAST_PKTS, 2): 0,
        indexed_oid(OID_IF_OUT_UCAST_PKTS, 2): 0,
        indexed_oid(OID_IF_IN_ERRORS, 2): 12,
        indexed_oid(OID_IF_OUT_ERRORS, 2): 0,
    }
    table_rows = {
        OID_IF_NAME: [
            (f"{OID_IF_NAME}.1", "GigabitEthernet0/1"),
            (f"{OID_IF_NAME}.2", "GigabitEthernet0/2"),
        ]
    }
    return scalars, table_rows


@pytest.fixture(autouse=True)
def _snmp_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SNMP_VERSION", "v2c")
    monkeypatch.setenv("SNMP_COMMUNITY_ROUTER_01", "public")


def _authorized_device() -> DeviceConfig:
    return DeviceConfig(device_id="router-01", ip="10.0.0.1", authorized=True, port=161)


def test_poll_device_returns_correct_sample_for_reachable_device() -> None:
    scalars, table_rows = _make_two_interface_fixture()

    def factory(ip, port, credentials, timeout_seconds, retries):
        return FakeSnmpTransport(ip, port, credentials, timeout_seconds, retries, scalars, table_rows)

    source = SNMPSource(transport_factory=factory)
    sample = asyncio.run(source.poll_device(_authorized_device()))

    assert sample.reachable is True
    assert sample.device_id == "router-01"
    assert sample.uptime_seconds == 1234  # 123456 TimeTicks / 100
    assert len(sample.interfaces) == 2

    eth1 = next(i for i in sample.interfaces if i.if_index == 1)
    assert eth1.if_name == "GigabitEthernet0/1"
    assert eth1.if_oper_status is True
    assert eth1.if_speed_bps == 1_000_000_000  # 1000 Mbps -> bps
    assert eth1.in_octets == 5_000_000
    assert eth1.out_octets == 3_000_000

    eth2 = next(i for i in sample.interfaces if i.if_index == 2)
    assert eth2.if_oper_status is False
    assert eth2.in_errors == 12


def test_poll_device_marks_unreachable_on_timeout() -> None:
    def factory(ip, port, credentials, timeout_seconds, retries):
        return FakeSnmpTransport(ip, port, credentials, timeout_seconds, retries, unreachable=True)

    source = SNMPSource(transport_factory=factory)
    sample = asyncio.run(source.poll_device(_authorized_device()))

    assert sample.reachable is False
    assert sample.uptime_seconds is None
    assert sample.interfaces == []
    assert sample.error_message is not None


def test_poll_device_refuses_unauthorized_device() -> None:
    scalars, table_rows = _make_two_interface_fixture()

    def factory(ip, port, credentials, timeout_seconds, retries):
        return FakeSnmpTransport(ip, port, credentials, timeout_seconds, retries, scalars, table_rows)

    source = SNMPSource(transport_factory=factory)
    unauthorized_device = DeviceConfig(device_id="router-01", ip="10.0.0.1", authorized=False)

    with pytest.raises(PermissionError, match="authorized"):
        asyncio.run(source.poll_device(unauthorized_device))


def test_transport_is_closed_after_poll() -> None:
    scalars, table_rows = _make_two_interface_fixture()
    created_transports: list[FakeSnmpTransport] = []

    def factory(ip, port, credentials, timeout_seconds, retries):
        t = FakeSnmpTransport(ip, port, credentials, timeout_seconds, retries, scalars, table_rows)
        created_transports.append(t)
        return t

    source = SNMPSource(transport_factory=factory)
    asyncio.run(source.poll_device(_authorized_device()))

    assert len(created_transports) == 1
    assert created_transports[0].closed is True


def test_interface_that_disappears_mid_poll_is_skipped_not_fatal() -> None:
    """One interface's per-column GET times out after the walk found it -
    the device should still be reported reachable, with just that
    interface omitted, per the 'must not crash on partial failure' requirement.
    """
    scalars, table_rows = _make_two_interface_fixture()
    # Simulate interface 2 vanishing: remove one of its required OIDs.
    del scalars[indexed_oid(OID_IF_HIGH_SPEED, 2)]

    def factory(ip, port, credentials, timeout_seconds, retries):
        return FakeSnmpTransport(ip, port, credentials, timeout_seconds, retries, scalars, table_rows)

    source = SNMPSource(transport_factory=factory)
    sample = asyncio.run(source.poll_device(_authorized_device()))

    assert sample.reachable is True
    assert len(sample.interfaces) == 1
    assert sample.interfaces[0].if_index == 1
