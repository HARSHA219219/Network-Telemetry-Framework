from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import grpc
import pytest

from collector.grpc_client.client import TelemetryGrpcClient
from collector.grpc_client.telemetry_builder import build_batch
from collector.metrics.bandwidth import BandwidthResult, UtilizationResult
from collector.metrics.interface_metrics import InterfaceMetrics
from collector.ping.base import IcmpResult
from collector.sources.base import DeviceConfig, RawDeviceSample
from common import telemetry_pb2


def _rpc_error(code: grpc.StatusCode = grpc.StatusCode.UNAVAILABLE) -> grpc.aio.AioRpcError:
    return grpc.aio.AioRpcError(
        code=code,
        initial_metadata=grpc.aio.Metadata(),
        trailing_metadata=grpc.aio.Metadata(),
        details="simulated backend unavailable",
    )


DEVICE = DeviceConfig(device_id="router-01", ip="10.0.0.1", authorized=True)


def _device_sample(reachable: bool = True) -> RawDeviceSample:
    return RawDeviceSample(
        device_id="router-01", device_ip="10.0.0.1",
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        reachable=reachable,
    )


def _interface_metrics() -> list[InterfaceMetrics]:
    return [
        InterfaceMetrics(
            if_name="eth0", if_index=1,
            in_bandwidth=BandwidthResult(bits_per_second=800.0, valid=True),
            out_bandwidth=BandwidthResult(bits_per_second=400.0, valid=True),
            in_utilization=UtilizationResult(percent=50.0, valid=True),
            out_utilization=UtilizationResult(percent=25.0, valid=True),
        )
    ]


# --- telemetry_builder ---

def test_build_batch_includes_reachability_and_interface_metrics() -> None:
    batch = build_batch("collector-1", DEVICE, _device_sample(), _interface_metrics(), None)

    types = [r.metric_type for r in batch.records]
    assert "device_reachable" in types
    assert "bandwidth_in" in types
    assert "utilization_out" in types
    assert batch.collector_id == "collector-1"


def test_build_batch_unreachable_device_only_reports_reachability() -> None:
    batch = build_batch("collector-1", DEVICE, _device_sample(reachable=False), [], None)

    assert len(batch.records) == 1
    assert batch.records[0].metric_type == "device_reachable"
    assert batch.records[0].metric_value == 0.0


def test_build_batch_includes_latency_and_packet_loss() -> None:
    latency = IcmpResult(
        device_id="router-01", device_ip="10.0.0.1", reachable=True,
        avg_ms=5.0, min_ms=3.0, max_ms=8.0, packet_loss_percent=0.0,
        samples_sent=4, samples_received=4,
    )
    batch = build_batch("collector-1", DEVICE, _device_sample(), [], latency)

    types = [r.metric_type for r in batch.records]
    assert "latency_avg_ms" in types
    assert "packet_loss_percent" in types


def test_build_batch_skips_invalid_bandwidth_result() -> None:
    metrics = [
        InterfaceMetrics(
            if_name="eth0", if_index=1,
            in_bandwidth=BandwidthResult(bits_per_second=0.0, valid=False, reason="counter_reset"),
            out_bandwidth=BandwidthResult(bits_per_second=400.0, valid=True),
            in_utilization=UtilizationResult(percent=None, valid=False, reason="bandwidth_unavailable"),
            out_utilization=UtilizationResult(percent=25.0, valid=True),
        )
    ]
    batch = build_batch("collector-1", DEVICE, _device_sample(), metrics, None)

    types = [r.metric_type for r in batch.records]
    assert "bandwidth_in" not in types  # invalid, correctly omitted
    assert "bandwidth_out" in types


# --- TelemetryGrpcClient ---

class _FakeStub:
    def __init__(self, fail_times: int = 0, exception: Exception | None = None) -> None:
        self.fail_times = fail_times
        self.calls = 0
        self.exception = exception or _rpc_error()

    async def SendTelemetryBatch(self, batch, timeout):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise self.exception
        return telemetry_pb2.SendTelemetryBatchResponse(accepted=True, records_stored=len(batch.records), message="ok")


def test_send_batch_succeeds_first_try() -> None:
    client = TelemetryGrpcClient(host="backend", port=50051, max_retries=3, retry_backoff_seconds=0.01)
    client._channel = object()  # bypass real channel creation
    client._stub = _FakeStub(fail_times=0)

    batch = telemetry_pb2.TelemetryBatch(collector_id="c1")
    response = asyncio.run(client.send_batch(batch))

    assert response is not None
    assert response.accepted is True
    assert client._stub.calls == 1


def test_send_batch_retries_then_succeeds() -> None:
    client = TelemetryGrpcClient(host="backend", port=50051, max_retries=3, retry_backoff_seconds=0.01)
    client._channel = object()
    fake_stub = _FakeStub(fail_times=2)
    client._stub = fake_stub

    batch = telemetry_pb2.TelemetryBatch(collector_id="c1")
    response = asyncio.run(client.send_batch(batch))

    assert response is not None
    assert response.accepted is True
    assert fake_stub.calls == 3  # 2 failures + 1 success


def test_send_batch_returns_none_after_exhausting_retries_without_crashing() -> None:
    client = TelemetryGrpcClient(host="backend", port=50051, max_retries=2, retry_backoff_seconds=0.01)
    client._channel = object()
    fake_stub = _FakeStub(fail_times=100)  # always fails
    client._stub = fake_stub

    batch = telemetry_pb2.TelemetryBatch(collector_id="c1")
    response = asyncio.run(client.send_batch(batch))

    assert response is None  # graceful - no exception propagated
    assert fake_stub.calls == 3  # 1 initial + 2 retries
