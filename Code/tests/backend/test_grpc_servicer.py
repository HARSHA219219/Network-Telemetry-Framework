from __future__ import annotations

import asyncio

import pytest

from backend.alerts.engine import AlertEngine
from backend.database.engine import create_session_factory
from backend.grpc_server.servicer import TelemetryIngestionServicer
from backend.services.ingestion_service import TelemetryIngestionService
from common import telemetry_pb2

THRESHOLDS = {"bandwidth_utilization_percent": 80, "latency_ms": 100, "packet_loss_percent": 5}


@pytest.fixture
def servicer():
    session_factory = create_session_factory("sqlite:///:memory:")
    service = TelemetryIngestionService(session_factory, AlertEngine(session_factory, THRESHOLDS))
    return TelemetryIngestionServicer(service)


def _batch_with_records(*records: dict) -> telemetry_pb2.TelemetryBatch:
    batch = telemetry_pb2.TelemetryBatch(collector_id="collector-1")
    for r in records:
        rec = batch.records.add()
        rec.device_id = r.get("device_id", "")
        rec.device_ip = r.get("device_ip", "")
        rec.timestamp_unix_ms = r.get("timestamp_unix_ms", 1_700_000_000_000)
        rec.metric_type = r.get("metric_type", "")
        rec.metric_value = r.get("metric_value", 0.0)
        rec.unit = r.get("unit", "")
        rec.interface_name = r.get("interface_name", "")
        rec.interface_index = r.get("interface_index", -1)
    return batch


def test_send_telemetry_batch_stores_valid_records(servicer) -> None:
    batch = _batch_with_records(
        {"device_id": "router-01", "device_ip": "10.0.0.1", "metric_type": "bandwidth_in", "metric_value": 800.0}
    )

    response = asyncio.run(servicer.SendTelemetryBatch(batch, context=None))

    assert response.accepted is True
    assert response.records_stored == 1


def test_send_telemetry_batch_handles_empty_batch(servicer) -> None:
    batch = telemetry_pb2.TelemetryBatch(collector_id="collector-1")

    response = asyncio.run(servicer.SendTelemetryBatch(batch, context=None))

    assert response.accepted is True
    assert response.records_stored == 0


def test_send_telemetry_batch_skips_invalid_records_without_failing_whole_batch(servicer) -> None:
    batch = _batch_with_records(
        {"device_id": "router-01", "device_ip": "10.0.0.1", "metric_type": "bandwidth_in", "metric_value": 800.0},
        {"device_id": "", "metric_type": "bandwidth_in", "metric_value": 100.0},  # invalid: no device_id
    )

    response = asyncio.run(servicer.SendTelemetryBatch(batch, context=None))

    assert response.accepted is True
    assert response.records_stored == 1


def test_send_telemetry_batch_does_not_crash_on_internal_error(monkeypatch, servicer) -> None:
    def _boom(self, records):
        raise RuntimeError("simulated ingestion failure")

    monkeypatch.setattr(TelemetryIngestionService, "ingest_records", _boom)

    batch = _batch_with_records({"device_id": "router-01", "metric_type": "bandwidth_in", "metric_value": 1.0})
    response = asyncio.run(servicer.SendTelemetryBatch(batch, context=None))

    assert response.accepted is False
    assert "simulated ingestion failure" in response.message
