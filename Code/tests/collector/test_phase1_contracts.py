"""
Phase 1 smoke tests.

These don't test real SNMP or simulator logic yet (that's Phase 3/5) -
they only prove the shared abstractions in collector/sources/base.py and
collector/ping/base.py are well-formed and usable, since every later
phase builds directly on top of them.
"""

from __future__ import annotations

import asyncio

import pytest

from collector.sources.base import (
    DeviceConfig,
    InterfaceSample,
    MetricSource,
    RawDeviceSample,
)
from collector.ping.base import IcmpResult, LatencySource


class _FakeMetricSource(MetricSource):
    """Minimal concrete implementation, used only to prove the ABC works."""

    async def poll_device(self, device: DeviceConfig) -> RawDeviceSample:
        return RawDeviceSample(
            device_id=device.device_id,
            device_ip=device.ip,
            timestamp=RawDeviceSample.now(),
            reachable=True,
            uptime_seconds=123,
            interfaces=[
                InterfaceSample(
                    if_name="eth0",
                    if_index=1,
                    if_oper_status=True,
                    if_speed_bps=1_000_000_000,
                    in_octets=1000,
                    out_octets=2000,
                    in_packets=10,
                    out_packets=20,
                    in_errors=0,
                    out_errors=0,
                )
            ],
        )

    async def close(self) -> None:
        return None


class _FakeLatencySource(LatencySource):
    async def measure(self, device_id: str, device_ip: str) -> IcmpResult:
        return IcmpResult(
            device_id=device_id,
            device_ip=device_ip,
            reachable=True,
            avg_ms=5.0,
            min_ms=4.0,
            max_ms=6.0,
            packet_loss_percent=0.0,
            samples_sent=4,
            samples_received=4,
        )

    async def close(self) -> None:
        return None


def test_metric_source_contract_is_usable() -> None:
    source = _FakeMetricSource()
    device = DeviceConfig(device_id="router-01", ip="10.0.0.1")

    sample = asyncio.run(source.poll_device(device))

    assert sample.device_id == "router-01"
    assert sample.reachable is True
    assert len(sample.interfaces) == 1
    assert sample.interfaces[0].if_name == "eth0"

    asyncio.run(source.close())


def test_metric_source_cannot_be_instantiated_directly() -> None:
    with pytest.raises(TypeError):
        MetricSource()  # abstract - must be subclassed


def test_latency_source_contract_is_usable() -> None:
    source = _FakeLatencySource()

    result = asyncio.run(source.measure("router-01", "10.0.0.1"))

    assert result.reachable is True
    assert result.packet_loss_percent == 0.0

    asyncio.run(source.close())


def test_latency_source_cannot_be_instantiated_directly() -> None:
    with pytest.raises(TypeError):
        LatencySource()  # abstract - must be subclassed
