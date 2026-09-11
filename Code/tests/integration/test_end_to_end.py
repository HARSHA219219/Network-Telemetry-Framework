"""
End-to-end integration test exercising the real pipeline:

    SimulatorSource -> CollectorApp -> real gRPC socket -> backend servicer
    -> TelemetryIngestionService -> SQLite (standing in for PostgreSQL)
    -> AlertEngine

Unlike tests/backend/test_grpc_servicer.py (which calls the servicer
directly, in-process), this test binds an actual grpc.aio server to a
loopback TCP port and drives it through a real TelemetryGrpcClient, so
serialization over the wire is genuinely exercised, not just the Python
objects on either side of it.
"""

from __future__ import annotations

import asyncio

import grpc
import pytest

from backend.alerts.engine import AlertEngine
from backend.database.engine import create_session_factory
from backend.grpc_server.servicer import TelemetryIngestionServicer
from backend.models.orm import Alert, TelemetryMetric
from backend.services.ingestion_service import TelemetryIngestionService
from collector.grpc_client.client import TelemetryGrpcClient
from collector.main import CollectorApp
from collector.ping.base import IcmpResult, LatencySource
from collector.simulator.scenario_controller import ScenarioController
from collector.simulator.scenarios import Scenario
from collector.simulator.simulator_source import SimulatorSource
from collector.sources.base import DeviceConfig
from common import telemetry_pb2_grpc

THRESHOLDS = {"bandwidth_utilization_percent": 80, "latency_ms": 100, "packet_loss_percent": 5}


class _NoLatency(LatencySource):
    """Skips real/simulated ping entirely - this test is about the
    bandwidth/alert pipeline, not latency, and keeping this out avoids
    an unrelated dependency on timing-sensitive simulated packet loss."""

    async def measure(self, device_id: str, device_ip: str) -> IcmpResult:
        return IcmpResult(device_id=device_id, device_ip=device_ip, reachable=True, avg_ms=1.0, min_ms=1.0, max_ms=1.0)

    async def close(self) -> None:
        return None


@pytest.fixture
def session_factory():
    return create_session_factory("sqlite:///:memory:")


@pytest.fixture
def alert_engine(session_factory):
    return AlertEngine(session_factory, THRESHOLDS)


async def _run_pipeline(session_factory, alert_engine) -> None:
    ingestion_service = TelemetryIngestionService(session_factory, alert_engine)
    servicer = TelemetryIngestionServicer(ingestion_service)

    server = grpc.aio.server()
    telemetry_pb2_grpc.add_TelemetryIngestionServicer_to_server(servicer, server)
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()

    try:
        controller = ScenarioController()
        device = DeviceConfig(device_id="router-01", ip="10.0.0.1", authorized=False)  # simulator ignores authorized
        grpc_client = TelemetryGrpcClient(host="127.0.0.1", port=port, timeout_seconds=5.0)

        collector = CollectorApp(
            devices=[device],
            metric_source=SimulatorSource(controller=controller),
            latency_source=_NoLatency(),
            grpc_client=grpc_client,
            collection_interval=999,  # not used directly - test drives poll_once() itself
            collector_id="integration-test-collector",
        )

        # --- Phase A: HIGH_BANDWIDTH should breach and create exactly one ACTIVE alert ---
        controller.set_scenario(Scenario.HIGH_BANDWIDTH, device_id="router-01")

        await collector.poll_once()   # round 1: establishes baseline counters, no delta yet to compute bandwidth from
        await asyncio.sleep(0.1)
        await collector.poll_once()   # round 2: bandwidth/utilization now computable -> should breach 80% threshold
        await asyncio.sleep(0.1)
        await collector.poll_once()   # round 3: still breaching -> must NOT create a second alert (dedup)

        with session_factory() as session:
            from sqlalchemy import select

            metrics = list(session.execute(select(TelemetryMetric).where(TelemetryMetric.device_id == "router-01")).scalars())
            assert len(metrics) > 0, "expected telemetry rows to have been stored via the real gRPC path"

            active_alerts = list(
                session.execute(
                    select(Alert).where(Alert.device_id == "router-01", Alert.alert_type == "HIGH_BANDWIDTH", Alert.status == "ACTIVE")
                ).scalars()
            )
            # Two simulated interfaces (eth0, eth1) both independently breach
            # utilization_in (~90%) - one ACTIVE alert per interface is
            # correct. Dedup means exactly 2, not 4, despite 2 breaching
            # rounds (round 2 and round 3) - each interface's alert is
            # created once and then left alone while still breaching.
            assert len(active_alerts) == 2, (
                f"expected exactly one ACTIVE HIGH_BANDWIDTH alert per interface (2 interfaces, deduped across "
                f"2 breaching rounds), got {len(active_alerts)}"
            )
            assert {a.interface_name for a in active_alerts} == {"eth0", "eth1"}

        # --- Phase B: back to NORMAL should resolve the alert ---
        controller.set_scenario(Scenario.NORMAL, device_id="router-01")
        await asyncio.sleep(0.1)
        await collector.poll_once()

        with session_factory() as session:
            from sqlalchemy import select

            resolved = list(
                session.execute(
                    select(Alert).where(Alert.device_id == "router-01", Alert.alert_type == "HIGH_BANDWIDTH", Alert.status == "RESOLVED")
                ).scalars()
            )
            still_active = list(
                session.execute(
                    select(Alert).where(Alert.device_id == "router-01", Alert.alert_type == "HIGH_BANDWIDTH", Alert.status == "ACTIVE")
                ).scalars()
            )
            assert len(resolved) == 2, "expected both interfaces' HIGH_BANDWIDTH alerts to resolve once traffic normalized"
            assert len(still_active) == 0

        await collector.shutdown()
    finally:
        await server.stop(grace=1.0)


def test_full_pipeline_simulator_to_grpc_to_db_to_alert_lifecycle(session_factory, alert_engine) -> None:
    asyncio.run(_run_pipeline(session_factory, alert_engine))


async def _run_device_down_pipeline(session_factory, alert_engine) -> None:
    ingestion_service = TelemetryIngestionService(session_factory, alert_engine)
    servicer = TelemetryIngestionServicer(ingestion_service)

    server = grpc.aio.server()
    telemetry_pb2_grpc.add_TelemetryIngestionServicer_to_server(servicer, server)
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()

    try:
        controller = ScenarioController()
        device = DeviceConfig(device_id="switch-01", ip="10.0.0.3", authorized=False)
        grpc_client = TelemetryGrpcClient(host="127.0.0.1", port=port, timeout_seconds=5.0)

        collector = CollectorApp(
            devices=[device],
            metric_source=SimulatorSource(controller=controller),
            latency_source=_NoLatency(),
            grpc_client=grpc_client,
            collection_interval=999,
            collector_id="integration-test-collector",
        )

        controller.set_scenario(Scenario.DEVICE_DOWN, device_id="switch-01")
        await collector.poll_once()

        with session_factory() as session:
            from sqlalchemy import select

            active_down = list(
                session.execute(
                    select(Alert).where(Alert.device_id == "switch-01", Alert.alert_type == "DEVICE_DOWN", Alert.status == "ACTIVE")
                ).scalars()
            )
            assert len(active_down) == 1

        controller.set_scenario(Scenario.RECOVERY, device_id="switch-01")
        await collector.poll_once()

        with session_factory() as session:
            from sqlalchemy import select

            resolved_down = list(
                session.execute(
                    select(Alert).where(Alert.device_id == "switch-01", Alert.alert_type == "DEVICE_DOWN", Alert.status == "RESOLVED")
                ).scalars()
            )
            assert len(resolved_down) == 1

        await collector.shutdown()
    finally:
        await server.stop(grace=1.0)


def test_device_down_then_recovery_over_real_grpc(session_factory, alert_engine) -> None:
    asyncio.run(_run_device_down_pipeline(session_factory, alert_engine))
