"""
Demo-only helper for switching simulator scenarios during a live demo.

Not part of the collector implementation - this is a thin wrapper around
the existing, already-public APIs (collector.main.build_app_from_config,
SimulatorSource.controller, ScenarioController.set_scenario) so a scenario
can be triggered on demand without editing any source file.

Why this exists: the normal `python -m collector.main` process runs its
own poll loop in its own process with no external control surface (no
admin endpoint - see README "Future Enhancements"). This script builds
its own short-lived CollectorApp from the SAME config/collector.yaml (so
it sends to the same backend the real collector would), sets a scenario,
and runs a handful of poll rounds so the effect (an alert firing /
resolving) is visible through the REST API and Grafana exactly as if the
main collector process had done it.

Usage (PowerShell), backend already running and reachable per
config/collector.yaml's grpc.backend_host/backend_port:

    python scripts/demo_scenario.py --device router-01 --scenario HIGH_BANDWIDTH --rounds 4 --interval 3
    python scripts/demo_scenario.py --device router-01 --scenario NORMAL --rounds 2 --interval 3
    python scripts/demo_scenario.py --device switch-01 --scenario DEVICE_DOWN --rounds 1
    python scripts/demo_scenario.py --device switch-01 --scenario RECOVERY --rounds 1

If the backend is running natively on the host (not in Docker), set
config/collector.yaml's grpc.backend_host to "localhost" first - this is
the same requirement the real collector has (see README).
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from collector.main import build_app_from_config
from collector.simulator.scenarios import Scenario

logger = logging.getLogger(__name__)


async def run(device_id: str, scenario_name: str, rounds: int, interval: float) -> None:
    app = build_app_from_config()
    scenario = Scenario[scenario_name.upper()]

    if not any(d.device_id == device_id for d in app.devices):
        raise SystemExit(
            f"'{device_id}' is not in config/devices.yaml. Known devices: "
            f"{[d.device_id for d in app.devices]}"
        )

    # SimulatorSource publicly exposes `.controller` (collector/simulator/simulator_source.py).
    # This only works when collector.yaml mode: SIMULATION - matches this script's purpose.
    if not hasattr(app.metric_source, "controller"):
        raise SystemExit(
            "config/collector.yaml is not in SIMULATION mode - this demo script only "
            "works against the simulator, not real SNMP devices."
        )

    app.metric_source.controller.set_scenario(scenario, device_id=device_id)
    print(f"Scenario '{scenario.value}' set for device '{device_id}'.")
    print(f"Sending {rounds} poll round(s), {interval}s apart, per config/collector.yaml's grpc target ...")

    for i in range(rounds):
        await app.poll_once()
        print(f"  round {i + 1}/{rounds} sent")
        if i < rounds - 1:
            await asyncio.sleep(interval)

    await app.shutdown()
    print("Done. Check GET /alerts/active or Grafana for the effect.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    parser = argparse.ArgumentParser(description="Trigger a simulator scenario against a running backend.")
    parser.add_argument("--device", default="router-01", help="device_id from config/devices.yaml")
    parser.add_argument("--scenario", default="NORMAL", choices=[s.name for s in Scenario])
    parser.add_argument("--rounds", type=int, default=4, help="number of poll rounds to send")
    parser.add_argument("--interval", type=float, default=3.0, help="seconds between rounds")
    args = parser.parse_args()

    asyncio.run(run(args.device, args.scenario, args.rounds, args.interval))
