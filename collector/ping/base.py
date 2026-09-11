"""
Shared contract between real ICMP latency measurement and the simulated
latency generator. Mirrors the MetricSource pattern in
collector/sources/base.py: everything downstream depends only on
IcmpResult, never on how it was produced.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class IcmpResult:
    """Result of a latency probe against one device."""

    device_id: str
    device_ip: str
    reachable: bool
    avg_ms: float | None = None
    min_ms: float | None = None
    max_ms: float | None = None
    packet_loss_percent: float = 0.0
    samples_sent: int = 0
    samples_received: int = 0


class LatencySource(ABC):
    """Abstract source of latency/reachability measurements.

    Concrete implementations:
      - IcmpPingSource         (collector/ping/icmp_source.py)       -> real ICMP
      - SimulatedLatencySource (collector/simulator/latency_source.py) -> mock
    """

    @abstractmethod
    async def measure(self, device_id: str, device_ip: str) -> IcmpResult:
        """Run a latency probe against one device.

        Must NOT raise on unreachable devices; return IcmpResult with
        reachable=False and packet_loss_percent=100.0 instead.
        """
        raise NotImplementedError

    @abstractmethod
    async def close(self) -> None:
        raise NotImplementedError
