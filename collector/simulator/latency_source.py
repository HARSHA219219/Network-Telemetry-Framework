"""
Simulated LatencySource - implements the same LatencySource contract as
IcmpPingSource (Phase 4), producing scenario-driven latency/loss figures
instead of real ICMP measurements.
"""

from __future__ import annotations

from collector.ping.base import IcmpResult, LatencySource
from collector.simulator.scenario_controller import ScenarioController
from collector.simulator.scenarios import SCENARIO_LATENCY_PROFILE, Scenario

_PROBES_PER_MEASUREMENT = 4


class SimulatedLatencySource(LatencySource):
    def __init__(self, controller: ScenarioController | None = None) -> None:
        self.controller = controller or ScenarioController()

    async def measure(self, device_id: str, device_ip: str) -> IcmpResult:
        scenario = self.controller.get_scenario(device_id)
        avg_ms, min_ms, max_ms, loss_percent = SCENARIO_LATENCY_PROFILE[scenario]

        if scenario is Scenario.DEVICE_DOWN:
            return IcmpResult(
                device_id=device_id, device_ip=device_ip, reachable=False,
                packet_loss_percent=100.0,
                samples_sent=_PROBES_PER_MEASUREMENT, samples_received=0,
            )

        received = round(_PROBES_PER_MEASUREMENT * (1 - loss_percent / 100))
        return IcmpResult(
            device_id=device_id, device_ip=device_ip, reachable=True,
            avg_ms=avg_ms, min_ms=min_ms, max_ms=max_ms,
            packet_loss_percent=loss_percent,
            samples_sent=_PROBES_PER_MEASUREMENT, samples_received=received,
        )

    async def close(self) -> None:
        return None
