"""
Shared contract between the real SNMP data source and the simulator.

Everything downstream of a MetricSource (bandwidth calculation, gRPC
transport, storage, alerting) depends ONLY on the RawDeviceSample /
InterfaceSample dataclasses defined here. It must never know or care
whether the data came from SNMP or a simulator.

This is the seam that makes the collector testable today (against the
simulator) and swappable to real hardware later (SNMPSource) without
touching any other module.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class InterfaceSample:
    """One interface's raw counters/state at a single point in time.

    Field names intentionally mirror IF-MIB semantics (ifHCInOctets,
    ifOperStatus, etc.) so the SNMP source can populate this directly,
    while the simulator fabricates plausible values for the same shape.
    """

    if_name: str
    if_index: int
    if_oper_status: bool          # True = up, False = down
    if_speed_bps: int              # interface capacity, bits/sec
    in_octets: int                  # cumulative counter (64-bit preferred)
    out_octets: int
    in_packets: int
    out_packets: int
    in_errors: int
    out_errors: int


@dataclass
class RawDeviceSample:
    """One device's full poll result at a single point in time."""

    device_id: str
    device_ip: str
    timestamp: datetime
    reachable: bool                          # False = SNMP timeout / simulated outage
    uptime_seconds: int | None = None
    interfaces: list[InterfaceSample] = field(default_factory=list)
    error_message: str | None = None         # populated when reachable is False

    @staticmethod
    def now() -> datetime:
        return datetime.now(timezone.utc)


class DeviceConfig:
    """Device descriptor passed into a MetricSource.

    `authorized` gates real SNMP polling (see collector/snmp/snmp_source.py) -
    it has no effect in simulation mode. SNMP credentials (community string,
    v3 auth) are deliberately NOT stored here; they're loaded from
    environment variables at poll time (collector/snmp/credentials.py) so
    they never end up in a YAML file or in this in-memory object longer
    than necessary.
    """

    def __init__(
        self,
        device_id: str,
        ip: str,
        authorized: bool = False,
        port: int = 161,
        extra: dict | None = None,
    ) -> None:
        self.device_id = device_id
        self.ip = ip
        self.authorized = authorized
        self.port = port
        self.extra = extra or {}


class MetricSource(ABC):
    """Abstract source of device/interface telemetry.

    Concrete implementations:
      - SNMPSource        (collector/snmp/snmp_source.py)   -> real devices
      - SimulatorSource   (collector/simulator/simulator_source.py) -> mock devices

    Both must be fully interchangeable: main.py selects one based on
    config.mode and never branches on mode again after that.
    """

    @abstractmethod
    async def poll_device(self, device: DeviceConfig) -> RawDeviceSample:
        """Poll a single device and return its current raw sample.

        Must NOT raise on device-level failure (timeout, unreachable).
        Instead, return a RawDeviceSample with reachable=False and
        error_message set. Only raise for programmer errors / invalid
        configuration.
        """
        raise NotImplementedError

    @abstractmethod
    async def close(self) -> None:
        """Release any held resources (SNMP engine, sockets, etc.)."""
        raise NotImplementedError
