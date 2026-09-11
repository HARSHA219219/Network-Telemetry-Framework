"""
Wires the pure calculation engine (bandwidth.py) to the shared telemetry
format (InterfaceSample / RawDeviceSample from collector/sources/base.py).

This is intentionally a thin layer: it matches interfaces between two
consecutive polls by if_index, computes ACTUAL elapsed time from the two
samples' timestamps (never the configured collection_interval), and
delegates all the real math to bandwidth.py. Works identically whether
the samples came from SNMPSource or (later) SimulatorSource - it only
depends on the shared dataclasses, not on SNMP.
"""

from __future__ import annotations

from dataclasses import dataclass

from collector.metrics.bandwidth import (
    BandwidthResult,
    UtilizationResult,
    calculate_bandwidth,
    calculate_utilization,
)
from collector.sources.base import InterfaceSample, RawDeviceSample

DEFAULT_COUNTER_BITS = 64  # matches ifHCInOctets/ifHCOutOctets from the SNMP layer


@dataclass
class InterfaceMetrics:
    if_name: str
    if_index: int
    in_bandwidth: BandwidthResult
    out_bandwidth: BandwidthResult
    in_utilization: UtilizationResult
    out_utilization: UtilizationResult


def calculate_interface_metrics(
    previous: InterfaceSample,
    current: InterfaceSample,
    elapsed_seconds: float,
    counter_bits: int = DEFAULT_COUNTER_BITS,
) -> InterfaceMetrics:
    """Calculate in/out bandwidth + utilization for one interface across two polls.

    `elapsed_seconds` must be the actual measured time between the two
    samples' timestamps - callers should not pass the configured
    collection_interval here.
    """
    in_bw = calculate_bandwidth(
        previous.in_octets, current.in_octets, elapsed_seconds, counter_bits
    )
    out_bw = calculate_bandwidth(
        previous.out_octets, current.out_octets, elapsed_seconds, counter_bits
    )

    in_util = (
        calculate_utilization(in_bw.bits_per_second, current.if_speed_bps)
        if in_bw.valid
        else UtilizationResult(percent=None, valid=False, reason="bandwidth_unavailable")
    )
    out_util = (
        calculate_utilization(out_bw.bits_per_second, current.if_speed_bps)
        if out_bw.valid
        else UtilizationResult(percent=None, valid=False, reason="bandwidth_unavailable")
    )

    return InterfaceMetrics(
        if_name=current.if_name,
        if_index=current.if_index,
        in_bandwidth=in_bw,
        out_bandwidth=out_bw,
        in_utilization=in_util,
        out_utilization=out_util,
    )


def calculate_device_metrics(
    previous: RawDeviceSample,
    current: RawDeviceSample,
    counter_bits: int = DEFAULT_COUNTER_BITS,
) -> list[InterfaceMetrics]:
    """Calculate metrics for every interface present in both polls of a device.

    Interfaces are matched by if_index. An interface present in `current`
    but not in `previous` (newly discovered) is skipped for this interval -
    there's nothing to diff against yet; it'll be included starting next poll.
    Elapsed time is computed from the two samples' actual timestamps.
    """
    if not previous.reachable or not current.reachable:
        return []

    elapsed_seconds = (current.timestamp - previous.timestamp).total_seconds()

    previous_by_index = {iface.if_index: iface for iface in previous.interfaces}

    results: list[InterfaceMetrics] = []
    for current_iface in current.interfaces:
        previous_iface = previous_by_index.get(current_iface.if_index)
        if previous_iface is None:
            continue  # newly discovered interface, no prior sample to diff against
        results.append(
            calculate_interface_metrics(previous_iface, current_iface, elapsed_seconds, counter_bits)
        )
    return results
