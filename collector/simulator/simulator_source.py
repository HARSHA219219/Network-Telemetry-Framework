"""
Simulator MetricSource - a SECONDARY data source for development, testing,
and demos when authorized physical device access is unavailable.

Critically, this produces RAW CUMULATIVE COUNTERS (in_octets, out_octets,
etc.) that increase over time according to the active scenario's
throughput profile - it does NOT precompute bandwidth/utilization
values. That means Phase 3's calculate_bandwidth()/calculate_utilization()
run against simulator output exactly as they would against real SNMP
output, with no special-casing anywhere in the metrics engine.
"""

from __future__ import annotations

from collector.simulator.scenario_controller import ScenarioController
from collector.simulator.scenarios import SCENARIO_THROUGHPUT_BYTES_PER_SECOND, Scenario
from collector.sources.base import DeviceConfig, InterfaceSample, MetricSource, RawDeviceSample

# Fixed simulated interface topology: two interfaces per device, both 1 Gbps.
# Kept simple and predictable (not configurable) because the point of the
# simulator is deterministic demoability, not modeling arbitrary hardware.
_SIMULATED_INTERFACES = (
    {"if_index": 1, "if_name": "eth0", "speed_bps": 1_000_000_000},
    {"if_index": 2, "if_name": "eth1", "speed_bps": 1_000_000_000},
)

# Average bytes per packet, used only to derive a plausible packet count
# from the byte delta - not metrically important, just keeps ifIn/OutUcastPkts
# non-zero and roughly proportional to traffic, as a real device would show.
_ASSUMED_AVG_PACKET_SIZE_BYTES = 1200


class SimulatorSource(MetricSource):
    def __init__(self, controller: ScenarioController | None = None) -> None:
        self.controller = controller or ScenarioController()
        self._counters: dict[str, dict[int, dict[str, int]]] = {}
        self._last_poll_time: dict[str, "object"] = {}
        self._device_start_time: dict[str, object] = {}

    async def poll_device(self, device: DeviceConfig) -> RawDeviceSample:
        now = RawDeviceSample.now()
        scenario = self.controller.get_scenario(device.device_id)

        self._device_start_time.setdefault(device.device_id, now)

        if scenario is Scenario.DEVICE_DOWN:
            # Do NOT advance counters while "down" - a real device's
            # counters don't move either while it's unreachable.
            self._last_poll_time[device.device_id] = now
            return RawDeviceSample(
                device_id=device.device_id,
                device_ip=device.ip,
                timestamp=now,
                reachable=False,
                error_message="Simulated DEVICE_DOWN scenario active",
            )

        previous_time = self._last_poll_time.get(device.device_id)
        elapsed_seconds = (now - previous_time).total_seconds() if previous_time else 1.0
        elapsed_seconds = max(elapsed_seconds, 0.001)  # guard against a zero-length interval
        self._last_poll_time[device.device_id] = now

        in_rate_bps, out_rate_bps = SCENARIO_THROUGHPUT_BYTES_PER_SECOND[scenario]
        device_counters = self._counters.setdefault(device.device_id, {})

        interfaces: list[InterfaceSample] = []
        for spec in _SIMULATED_INTERFACES:
            if_index = spec["if_index"]
            state = device_counters.setdefault(
                if_index, {"in_octets": 0, "out_octets": 0, "in_packets": 0, "out_packets": 0}
            )

            in_delta = int(in_rate_bps * elapsed_seconds)
            out_delta = int(out_rate_bps * elapsed_seconds)
            state["in_octets"] += in_delta
            state["out_octets"] += out_delta
            state["in_packets"] += max(0, in_delta // _ASSUMED_AVG_PACKET_SIZE_BYTES)
            state["out_packets"] += max(0, out_delta // _ASSUMED_AVG_PACKET_SIZE_BYTES)

            interfaces.append(
                InterfaceSample(
                    if_name=spec["if_name"],
                    if_index=if_index,
                    if_oper_status=True,
                    if_speed_bps=spec["speed_bps"],
                    in_octets=state["in_octets"],
                    out_octets=state["out_octets"],
                    in_packets=state["in_packets"],
                    out_packets=state["out_packets"],
                    in_errors=0,
                    out_errors=0,
                )
            )

        uptime_seconds = int((now - self._device_start_time[device.device_id]).total_seconds())

        return RawDeviceSample(
            device_id=device.device_id,
            device_ip=device.ip,
            timestamp=now,
            reachable=True,
            uptime_seconds=uptime_seconds,
            interfaces=interfaces,
        )

    async def close(self) -> None:
        return None
