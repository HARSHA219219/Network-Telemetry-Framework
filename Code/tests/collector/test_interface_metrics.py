from __future__ import annotations

from datetime import datetime, timedelta, timezone

from collector.metrics.interface_metrics import calculate_device_metrics, calculate_interface_metrics
from collector.sources.base import InterfaceSample, RawDeviceSample


def _iface(
    if_index: int = 1,
    if_name: str = "eth0",
    in_octets: int = 0,
    out_octets: int = 0,
    speed_bps: int = 1_000_000_000,
) -> InterfaceSample:
    return InterfaceSample(
        if_name=if_name,
        if_index=if_index,
        if_oper_status=True,
        if_speed_bps=speed_bps,
        in_octets=in_octets,
        out_octets=out_octets,
        in_packets=0,
        out_packets=0,
        in_errors=0,
        out_errors=0,
    )


def test_calculate_interface_metrics_uses_actual_elapsed_seconds() -> None:
    previous = _iface(in_octets=1000, out_octets=2000)
    current = _iface(in_octets=2000, out_octets=4000)

    metrics = calculate_interface_metrics(previous, current, elapsed_seconds=10)

    assert metrics.in_bandwidth.valid is True
    assert metrics.in_bandwidth.bits_per_second == (1000 * 8) / 10
    assert metrics.out_bandwidth.bits_per_second == (2000 * 8) / 10
    assert metrics.in_utilization.valid is True
    assert metrics.out_utilization.valid is True


def test_calculate_device_metrics_uses_real_timestamp_difference_not_a_config_value() -> None:
    t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(seconds=7)  # deliberately not a "round" configured interval like 10

    previous = RawDeviceSample(
        device_id="router-01", device_ip="10.0.0.1", timestamp=t0, reachable=True,
        interfaces=[_iface(in_octets=0, out_octets=0)],
    )
    current = RawDeviceSample(
        device_id="router-01", device_ip="10.0.0.1", timestamp=t1, reachable=True,
        interfaces=[_iface(in_octets=7000, out_octets=0)],
    )

    results = calculate_device_metrics(previous, current)

    assert len(results) == 1
    # 7000 bytes * 8 / 7s = 8000 bps - proves elapsed came from timestamps (7s), not a guess
    assert results[0].in_bandwidth.bits_per_second == 8000.0


def test_calculate_device_metrics_matches_interfaces_by_index() -> None:
    t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(seconds=10)

    previous = RawDeviceSample(
        device_id="router-01", device_ip="10.0.0.1", timestamp=t0, reachable=True,
        interfaces=[_iface(if_index=1, in_octets=1000), _iface(if_index=2, in_octets=5000)],
    )
    current = RawDeviceSample(
        device_id="router-01", device_ip="10.0.0.1", timestamp=t1, reachable=True,
        interfaces=[_iface(if_index=2, in_octets=6000), _iface(if_index=1, in_octets=2000)],
    )

    results = calculate_device_metrics(previous, current)
    by_index = {r.if_index: r for r in results}

    assert len(results) == 2
    assert by_index[1].in_bandwidth.bits_per_second == (1000 * 8) / 10
    assert by_index[2].in_bandwidth.bits_per_second == (1000 * 8) / 10


def test_newly_discovered_interface_is_skipped_for_this_interval() -> None:
    t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(seconds=10)

    previous = RawDeviceSample(
        device_id="router-01", device_ip="10.0.0.1", timestamp=t0, reachable=True,
        interfaces=[_iface(if_index=1, in_octets=1000)],
    )
    current = RawDeviceSample(
        device_id="router-01", device_ip="10.0.0.1", timestamp=t1, reachable=True,
        interfaces=[_iface(if_index=1, in_octets=2000), _iface(if_index=99, in_octets=500)],
    )

    results = calculate_device_metrics(previous, current)

    assert len(results) == 1
    assert results[0].if_index == 1


def test_unreachable_sample_produces_no_metrics() -> None:
    t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(seconds=10)

    previous = RawDeviceSample(
        device_id="router-01", device_ip="10.0.0.1", timestamp=t0, reachable=True,
        interfaces=[_iface(if_index=1, in_octets=1000)],
    )
    current = RawDeviceSample(
        device_id="router-01", device_ip="10.0.0.1", timestamp=t1, reachable=False,
        interfaces=[],
    )

    assert calculate_device_metrics(previous, current) == []
