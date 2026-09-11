"""
Shared mutable scenario state, referenced by both SimulatorSource and
SimulatedLatencySource so a single `set_scenario()` call (e.g. from a
demo script or a future admin endpoint) consistently affects both
counter generation and latency generation for a device.
"""

from __future__ import annotations

from collector.simulator.scenarios import Scenario


class ScenarioController:
    def __init__(self, default_scenario: Scenario = Scenario.NORMAL) -> None:
        self._default = default_scenario
        self._overrides: dict[str, Scenario] = {}

    def set_scenario(self, scenario: Scenario, device_id: str | None = None) -> None:
        """Set the scenario globally (device_id=None) or for one device."""
        if device_id is None:
            self._default = scenario
            self._overrides.clear()
        else:
            self._overrides[device_id] = scenario

    def get_scenario(self, device_id: str) -> Scenario:
        return self._overrides.get(device_id, self._default)

    def clear_override(self, device_id: str) -> None:
        self._overrides.pop(device_id, None)
