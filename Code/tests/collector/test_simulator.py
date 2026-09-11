from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from collector.metrics.interface_metrics import calculate_device_metrics
from collector.simulator.latency_source import SimulatedLatencySource
from collector.simulator.scenario_controller import ScenarioController
from collector.simulator.scenarios import Scenario
from collector.simulator.simulator_source import SimulatorSource
from collector.sources.base import DeviceConfig, RawDeviceSample

DEVICE = DeviceConfig(device_id="sim-router-01", ip="192.0.2.1")


def _patch_clock(monkeypatch: pytest.MonkeyPatch, *timestamps: datetime) -> None:
    """Make RawDeviceSample.now() return each timestamp in sequence.

    Needed because the simulator uses this same clock both to compute its
    internal elapsed-time-based counter deltas AND to stamp the returned
    sample - they must agree, or downstream bandwidth calculation (which
    uses the real timestamp difference, per Phase 3) will disagree with
    what the simulator intended to simulate.
    """
    iterator = iter(timestamps)
    monkeypatch.setattr(RawDeviceSample, "now", staticmethod(lambda: next(iterator)))


def test_normal_scenario_produces_moderate_utilization(monkeypatch: pytest.MonkeyPatch) -> None:
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    t1 = t0 + timedelta(seconds=10)
    _patch_clock(monkeypatch, t0, t1)

    controller = ScenarioController(default_scenario=Scenario.NORMAL)
    source = SimulatorSource(controller)

    sample1 = asyncio.run(source.poll_device(DEVICE))
    sample2 = asyncio.run(source.poll_device(DEVICE))

    assert sample1.reachable is True
    assert sample2.reachable is True

    metrics = calculate_device_metrics(sample1, sample2)
    assert len(metrics) == 2
    eth0 = next(m for m in metrics if m.if_index == 1)
    assert eth0.in_bandwidth.valid is True
    assert eth0.in_utilization.percent is not None
    assert eth0.in_utilization.percent < 50  # "moderate" per NORMAL profile


def test_high_bandwidth_scenario_crosses_default_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    t1 = t0 + timedelta(seconds=10)
    _patch_clock(monkeypatch, t0, t1)

    controller = ScenarioController(default_scenario=Scenario.HIGH_BANDWIDTH)
    source = SimulatorSource(controller)

    sample1 = asyncio.run(source.poll_device(DEVICE))
    sample2 = asyncio.run(source.poll_device(DEVICE))

    metrics = calculate_device_metrics(sample1, sample2)
    eth0 = next(m for m in metrics if m.if_index == 1)

    assert eth0.in_utilization.percent > 80  # default bandwidth_utilization threshold from thresholds.yaml


def test_high_latency_scenario() -> None:
    controller = ScenarioController(default_scenario=Scenario.HIGH_LATENCY)
    latency_source = SimulatedLatencySource(controller)

    result = asyncio.run(latency_source.measure(DEVICE.device_id, DEVICE.ip))

    assert result.reachable is True
    assert result.avg_ms > 100  # default latency_ms threshold


def test_packet_loss_scenario() -> None:
    controller = ScenarioController(default_scenario=Scenario.PACKET_LOSS)
    latency_source = SimulatedLatencySource(controller)

    result = asyncio.run(latency_source.measure(DEVICE.device_id, DEVICE.ip))

    assert result.packet_loss_percent > 5  # default packet_loss_percent threshold
    assert result.samples_received < result.samples_sent


def test_device_down_scenario_marks_unreachable_for_both_sources() -> None:
    controller = ScenarioController(default_scenario=Scenario.DEVICE_DOWN)
    metric_source = SimulatorSource(controller)
    latency_source = SimulatedLatencySource(controller)

    device_sample = asyncio.run(metric_source.poll_device(DEVICE))
    latency_result = asyncio.run(latency_source.measure(DEVICE.device_id, DEVICE.ip))

    assert device_sample.reachable is False
    assert device_sample.interfaces == []
    assert latency_result.reachable is False
    assert latency_result.packet_loss_percent == 100.0


def test_recovery_scenario_resumes_normal_traffic_after_down() -> None:
    controller = ScenarioController(default_scenario=Scenario.DEVICE_DOWN)
    source = SimulatorSource(controller)

    down_sample = asyncio.run(source.poll_device(DEVICE))
    assert down_sample.reachable is False

    controller.set_scenario(Scenario.RECOVERY, device_id=DEVICE.device_id)
    recovered_sample = asyncio.run(source.poll_device(DEVICE))

    assert recovered_sample.reachable is True
    assert len(recovered_sample.interfaces) == 2


def test_counters_are_monotonically_increasing_across_polls(monkeypatch: pytest.MonkeyPatch) -> None:
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    t1 = t0 + timedelta(seconds=5)
    _patch_clock(monkeypatch, t0, t1)

    controller = ScenarioController(default_scenario=Scenario.NORMAL)
    source = SimulatorSource(controller)

    sample1 = asyncio.run(source.poll_device(DEVICE))
    sample2 = asyncio.run(source.poll_device(DEVICE))

    eth0_s1 = next(i for i in sample1.interfaces if i.if_index == 1)
    eth0_s2 = next(i for i in sample2.interfaces if i.if_index == 1)

    # Counters must only ever go up during normal operation - this is what
    # lets Phase 3's engine treat them exactly like real SNMP counters.
    assert eth0_s2.in_octets >= eth0_s1.in_octets
    assert eth0_s2.out_octets >= eth0_s1.out_octets


def test_per_device_scenario_override_does_not_affect_other_devices() -> None:
    controller = ScenarioController(default_scenario=Scenario.NORMAL)
    controller.set_scenario(Scenario.DEVICE_DOWN, device_id="router-a")

    assert controller.get_scenario("router-a") is Scenario.DEVICE_DOWN
    assert controller.get_scenario("router-b") is Scenario.NORMAL


def test_simulator_conforms_to_metric_source_contract() -> None:
    from collector.sources.base import MetricSource

    assert isinstance(SimulatorSource(), MetricSource)


def test_simulated_latency_conforms_to_latency_source_contract() -> None:
    from collector.ping.base import LatencySource

    assert isinstance(SimulatedLatencySource(), LatencySource)
