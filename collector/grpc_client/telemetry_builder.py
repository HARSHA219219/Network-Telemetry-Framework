"""
Builds a protobuf TelemetryBatch from a device's raw sample, computed
interface metrics (Phase 3 output), and a latency measurement (Phase 4
output). Pure conversion logic - no network calls - so it can be unit
tested without a gRPC channel.
"""

from __future__ import annotations

from collector.metrics.interface_metrics import InterfaceMetrics
from collector.ping.base import IcmpResult
from collector.sources.base import DeviceConfig, RawDeviceSample
from common import telemetry_pb2

NO_INTERFACE_INDEX = -1


def build_batch(
    collector_id: str,
    device: DeviceConfig,
    device_sample: RawDeviceSample,
    interface_metrics: list[InterfaceMetrics],
    latency_result: IcmpResult | None,
) -> telemetry_pb2.TelemetryBatch:
    batch = telemetry_pb2.TelemetryBatch(collector_id=collector_id)
    timestamp_ms = int(device_sample.timestamp.timestamp() * 1000)

    reachable_record = batch.records.add()
    reachable_record.device_id = device.device_id
    reachable_record.device_ip = device.ip
    reachable_record.timestamp_unix_ms = timestamp_ms
    reachable_record.metric_type = "device_reachable"
    reachable_record.metric_value = 1.0 if device_sample.reachable else 0.0
    reachable_record.unit = "bool"
    reachable_record.interface_index = NO_INTERFACE_INDEX

    if not device_sample.reachable:
        return batch  # nothing else meaningful to report for this poll

    for metrics in interface_metrics:
        _add_if_valid(batch, device, timestamp_ms, "bandwidth_in", metrics.in_bandwidth.bits_per_second,
                       "bps", metrics.if_name, metrics.if_index, metrics.in_bandwidth.valid)
        _add_if_valid(batch, device, timestamp_ms, "bandwidth_out", metrics.out_bandwidth.bits_per_second,
                       "bps", metrics.if_name, metrics.if_index, metrics.out_bandwidth.valid)
        _add_if_valid(batch, device, timestamp_ms, "utilization_in", metrics.in_utilization.percent,
                       "percent", metrics.if_name, metrics.if_index, metrics.in_utilization.valid)
        _add_if_valid(batch, device, timestamp_ms, "utilization_out", metrics.out_utilization.percent,
                       "percent", metrics.if_name, metrics.if_index, metrics.out_utilization.valid)

    if latency_result is not None:
        if latency_result.reachable:
            for metric_type, value in (
                ("latency_avg_ms", latency_result.avg_ms),
                ("latency_min_ms", latency_result.min_ms),
                ("latency_max_ms", latency_result.max_ms),
            ):
                _add_if_valid(batch, device, timestamp_ms, metric_type, value, "ms", "", NO_INTERFACE_INDEX, value is not None)

        loss_record = batch.records.add()
        loss_record.device_id = device.device_id
        loss_record.device_ip = device.ip
        loss_record.timestamp_unix_ms = timestamp_ms
        loss_record.metric_type = "packet_loss_percent"
        loss_record.metric_value = latency_result.packet_loss_percent
        loss_record.unit = "percent"
        loss_record.interface_index = NO_INTERFACE_INDEX

    return batch


def _add_if_valid(
    batch: telemetry_pb2.TelemetryBatch,
    device: DeviceConfig,
    timestamp_ms: int,
    metric_type: str,
    value: float | None,
    unit: str,
    interface_name: str,
    interface_index: int,
    valid: bool,
) -> None:
    if not valid or value is None:
        return
    record = batch.records.add()
    record.device_id = device.device_id
    record.device_ip = device.ip
    record.timestamp_unix_ms = timestamp_ms
    record.metric_type = metric_type
    record.metric_value = value
    record.unit = unit
    record.interface_name = interface_name
    record.interface_index = interface_index
