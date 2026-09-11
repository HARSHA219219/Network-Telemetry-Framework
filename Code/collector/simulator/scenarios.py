"""
Simulator scenarios.

Deliberately deterministic/scriptable rather than pure-random: a demo
needs to reliably trigger "now show a bandwidth alert" on cue. Each
scenario controls how the NEXT counter delta is generated - the
simulator still emits raw cumulative counters, never a precomputed
bandwidth number, so Phase 3's calculation engine is genuinely exercised
end-to-end rather than bypassed.
"""

from __future__ import annotations

from enum import Enum


class Scenario(str, Enum):
    NORMAL = "NORMAL"
    HIGH_BANDWIDTH = "HIGH_BANDWIDTH"
    HIGH_LATENCY = "HIGH_LATENCY"
    PACKET_LOSS = "PACKET_LOSS"
    DEVICE_DOWN = "DEVICE_DOWN"
    RECOVERY = "RECOVERY"


# Bytes/sec of simulated traffic to add per second of elapsed time, per scenario.
# Interface speed in the simulator is fixed at 1 Gbps (see simulator_source.py),
# so these are chosen to land at clearly distinguishable utilization bands.
SCENARIO_THROUGHPUT_BYTES_PER_SECOND: dict[Scenario, tuple[int, int]] = {
    # (in_bytes_per_sec, out_bytes_per_sec) - roughly 10-20% utilization
    Scenario.NORMAL: (12_500_000, 8_000_000),
    # ~90% utilization on a 1 Gbps interface -> crosses the default 80% threshold
    Scenario.HIGH_BANDWIDTH: (112_500_000, 100_000_000),
    Scenario.HIGH_LATENCY: (12_500_000, 8_000_000),   # traffic unaffected; latency model handles this scenario
    Scenario.PACKET_LOSS: (12_500_000, 8_000_000),      # traffic unaffected; loss modeled in latency source
    Scenario.DEVICE_DOWN: (0, 0),                          # unreachable - counters don't advance
    Scenario.RECOVERY: (12_500_000, 8_000_000),          # back to normal traffic levels
}

# (avg_ms, min_ms, max_ms, loss_percent) per scenario for the simulated latency source.
SCENARIO_LATENCY_PROFILE: dict[Scenario, tuple[float, float, float, float]] = {
    Scenario.NORMAL: (5.0, 3.0, 8.0, 0.0),
    Scenario.HIGH_BANDWIDTH: (15.0, 10.0, 25.0, 0.0),
    Scenario.HIGH_LATENCY: (150.0, 120.0, 200.0, 0.0),   # crosses the default 100ms threshold
    Scenario.PACKET_LOSS: (20.0, 10.0, 40.0, 15.0),        # crosses the default 5% threshold
    Scenario.DEVICE_DOWN: (0.0, 0.0, 0.0, 100.0),
    Scenario.RECOVERY: (6.0, 4.0, 9.0, 0.0),
}
