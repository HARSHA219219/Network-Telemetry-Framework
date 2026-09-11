from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from backend.alerts.engine import AlertEngine
from backend.database.engine import create_session_factory
from backend.models.orm import Alert, Device, Interface, TelemetryMetric
from backend.services.ingestion_service import TelemetryIngestionService

THRESHOLDS = {"bandwidth_utilization_percent": 80, "latency_ms": 100, "packet_loss_percent": 5}


@pytest.fixture
def session_factory():
    return create_session_factory("sqlite:///:memory:")


@pytest.fixture
def service(session_factory):
    return TelemetryIngestionService(session_factory, AlertEngine(session_factory, THRESHOLDS))


def _record(**overrides) -> dict:
    base = {
        "device_id": "router-01",
        "device_ip": "10.0.0.1",
        "timestamp": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "metric_type": "bandwidth_in",
        "metric_value": 800.0,
        "unit": "bps",
        "interface_name": "eth0",
        "interface_index": 1,
    }
    base.update(overrides)
    return base


def test_ingest_creates_device_and_metric(service, session_factory) -> None:
    stored = service.ingest_records([_record()])

    assert stored == 1
    with session_factory() as session:
        device = session.execute(select(Device).where(Device.device_id == "router-01")).scalars().first()
        metric = session.execute(select(TelemetryMetric)).scalars().first()

        assert device is not None
        assert device.device_ip == "10.0.0.1"
        assert metric.metric_value == 800.0
        assert metric.metric_type == "bandwidth_in"


def test_ingest_creates_interface(service, session_factory) -> None:
    service.ingest_records([_record()])

    with session_factory() as session:
        iface = session.execute(select(Interface).where(Interface.if_name == "eth0")).scalars().first()
        assert iface is not None
        assert iface.if_index == 1


def test_ingest_updates_existing_device_not_duplicate(service, session_factory) -> None:
    service.ingest_records([_record()])
    service.ingest_records([_record(device_ip="10.0.0.99")])

    with session_factory() as session:
        devices = session.execute(select(Device).where(Device.device_id == "router-01")).scalars().all()
        assert len(devices) == 1
        assert devices[0].device_ip == "10.0.0.99"  # updated


def test_ingest_skips_invalid_records_but_stores_valid_ones(service) -> None:
    valid = _record()
    invalid_no_device_id = _record(device_id="")
    invalid_bad_value = _record(metric_value="not-a-number")

    stored = service.ingest_records([valid, invalid_no_device_id, invalid_bad_value])

    assert stored == 1


def test_ingest_device_reachable_metric_updates_device_status(service, session_factory) -> None:
    service.ingest_records([_record(metric_type="device_reachable", metric_value=0.0, interface_name=None, interface_index=None)])

    with session_factory() as session:
        device = session.execute(select(Device).where(Device.device_id == "router-01")).scalars().first()
        assert device.reachable is False


def test_ingest_triggers_alert_engine_on_breach(service, session_factory) -> None:
    service.ingest_records([_record(metric_type="utilization_in", metric_value=91.0)])

    with session_factory() as session:
        alerts = session.execute(select(Alert).where(Alert.status == "ACTIVE")).scalars().all()
        assert len(alerts) == 1
        assert alerts[0].alert_type == "HIGH_BANDWIDTH"


def test_ingest_empty_batch_returns_zero(service) -> None:
    assert service.ingest_records([]) == 0
