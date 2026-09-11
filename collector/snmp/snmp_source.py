"""
Real SNMP MetricSource implementation.

Polls one device per poll_device() call:
  1. GET sysUpTime as a liveness check (timeout -> device marked unreachable).
  2. WALK ifName to discover current interfaces (see oids.py for why ifName).
  3. For each discovered interface, GET status/speed/counters/errors.

Produces exactly the same RawDeviceSample/InterfaceSample shape as
SimulatorSource - nothing downstream can tell which one produced it.
"""

from __future__ import annotations

import logging

from collector.snmp.credentials import load_snmp_credentials
from collector.snmp.exceptions import SnmpError, SnmpTimeoutError
from collector.snmp.oids import (
    IF_OPER_STATUS_UP,
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
    extract_index_suffix,
    indexed_oid,
)
from collector.snmp.transport import PysnmpTransport, SnmpTransport
from collector.sources.base import DeviceConfig, InterfaceSample, MetricSource, RawDeviceSample

logger = logging.getLogger(__name__)

# sysUpTime is a TimeTicks value: hundredths of a second since last reboot.
_TIMETICKS_PER_SECOND = 100


class SNMPSource(MetricSource):
    """MetricSource backed by real SNMPv2c polling.

    `transport_factory` is injectable so tests can substitute a fake
    transport with no real sockets/pysnmp involved - see
    tests/collector/test_snmp_source.py.
    """

    def __init__(
        self,
        timeout_seconds: float = 2.0,
        retries: int = 1,
        transport_factory: type[SnmpTransport] = PysnmpTransport,
    ) -> None:
        self._timeout_seconds = timeout_seconds
        self._retries = retries
        self._transport_factory = transport_factory

    async def poll_device(self, device: DeviceConfig) -> RawDeviceSample:
        if not device.authorized:
            raise PermissionError(
                f"Device '{device.device_id}' is not marked authorized:true in "
                f"devices.yaml - refusing to poll it over SNMP. This is a "
                f"deliberate safety gate, not a bug."
            )

        credentials = load_snmp_credentials(device.device_id)
        transport = self._transport_factory(
            device.ip, device.port, credentials, self._timeout_seconds, self._retries
        )

        try:
            try:
                raw_uptime = await transport.get(OID_SYS_UPTIME)
                uptime_seconds = int(raw_uptime) // _TIMETICKS_PER_SECOND
            except SnmpTimeoutError as exc:
                logger.warning("Device %s (%s) unreachable: %s", device.device_id, device.ip, exc)
                return RawDeviceSample(
                    device_id=device.device_id,
                    device_ip=device.ip,
                    timestamp=RawDeviceSample.now(),
                    reachable=False,
                    error_message=str(exc),
                )

            interfaces = await self._discover_and_poll_interfaces(device, transport)

            return RawDeviceSample(
                device_id=device.device_id,
                device_ip=device.ip,
                timestamp=RawDeviceSample.now(),
                reachable=True,
                uptime_seconds=uptime_seconds,
                interfaces=interfaces,
            )

        except SnmpError as exc:
            logger.error("SNMP error polling device %s (%s): %s", device.device_id, device.ip, exc)
            return RawDeviceSample(
                device_id=device.device_id,
                device_ip=device.ip,
                timestamp=RawDeviceSample.now(),
                reachable=False,
                error_message=str(exc),
            )
        finally:
            await transport.close()

    async def _discover_and_poll_interfaces(
        self, device: DeviceConfig, transport: SnmpTransport
    ) -> list[InterfaceSample]:
        name_rows = await transport.walk(OID_IF_NAME)

        interfaces: list[InterfaceSample] = []
        for full_oid, name_value in name_rows:
            if_index = extract_index_suffix(full_oid, OID_IF_NAME)
            if_name = str(name_value)

            try:
                oper_status = await transport.get(indexed_oid(OID_IF_OPER_STATUS, if_index))
                speed_mbps = await transport.get(indexed_oid(OID_IF_HIGH_SPEED, if_index))
                in_octets = await transport.get(indexed_oid(OID_IF_HC_IN_OCTETS, if_index))
                out_octets = await transport.get(indexed_oid(OID_IF_HC_OUT_OCTETS, if_index))
                in_packets = await transport.get(indexed_oid(OID_IF_IN_UCAST_PKTS, if_index))
                out_packets = await transport.get(indexed_oid(OID_IF_OUT_UCAST_PKTS, if_index))
                in_errors = await transport.get(indexed_oid(OID_IF_IN_ERRORS, if_index))
                out_errors = await transport.get(indexed_oid(OID_IF_OUT_ERRORS, if_index))
            except SnmpTimeoutError as exc:
                # Interface can vanish between the walk and the per-column
                # GETs (rare, but real - e.g. a sub-interface torn down
                # mid-poll). Skip it rather than failing the whole device.
                logger.warning(
                    "Skipping interface %s (index %d) on device %s: %s",
                    if_name, if_index, device.device_id, exc,
                )
                continue

            interfaces.append(
                InterfaceSample(
                    if_name=if_name,
                    if_index=if_index,
                    if_oper_status=(int(oper_status) == IF_OPER_STATUS_UP),
                    if_speed_bps=int(speed_mbps) * 1_000_000,
                    in_octets=int(in_octets),
                    out_octets=int(out_octets),
                    in_packets=int(in_packets),
                    out_packets=int(out_packets),
                    in_errors=int(in_errors),
                    out_errors=int(out_errors),
                )
            )

        return interfaces

    async def close(self) -> None:
        # No persistent resources are held across poll_device() calls -
        # a fresh transport is created and closed per poll.
        return None
