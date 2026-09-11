"""
Tests the FastAPI REST layer end-to-end (router -> service -> repository
-> DB) against an in-memory sqlite database - no real PostgreSQL needed,
consistent with the pattern used everywhere else in this project.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.database.engine import create_session_factory
from backend.models.orm import Alert, Device, TelemetryMetric


@pytest.fixture()
def client() -> TestClient:
    session_factory = create_session_factory("sqlite:///:memory:")
    app = create_app(session_factory)

    # seed some data directly via the ORM, independent of the ingestion path
    now = datetime.now(timezone.utc)
    with session_factory() as session:
        session.add(Device(device_id="router-01", device_ip="10.0.0.1", kind="router", reachable=True, last_seen=now))
        session.add(Device(device_id="router-02", device_ip="10.0.0.2", kind="router", reachable=False, last_seen=now))

        session.add(TelemetryMetric(
            device_id="router-01", device_ip="10.0.0.1", interface_name="eth0",
            metric_type="bandwidth_in", metric_value=500_000_000.0, unit="bps", timestamp=now,
        ))
        session.add(TelemetryMetric(
            device_id="router-01", device_ip="10.0.0.1", interface_name="eth0",
            metric_type="utilization_in", metric_value=50.0, unit="percent", timestamp=now,
        ))
        session.add(TelemetryMetric(
            device_id="router-01", device_ip="10.0.0.1", interface_name=None,
            metric_type="latency_avg_ms", metric_value=12.5, unit="ms", timestamp=now,
        ))
        session.add(TelemetryMetric(
            device_id="router-01", device_ip="10.0.0.1", interface_name=None,
            metric_type="bandwidth_in", metric_value=100.0, unit="bps",
            timestamp=now - timedelta(hours=2),
        ))

        session.add(Alert(
            device_id="router-01", interface_name="eth0", alert_type="HIGH_BANDWIDTH",
            severity="WARNING", metric_value=91.0, threshold=80.0, status="ACTIVE", started_at=now,
        ))
        session.add(Alert(
            device_id="router-02", interface_name=None, alert_type="DEVICE_DOWN",
            severity="CRITICAL", metric_value=0.0, threshold=1.0, status="RESOLVED",
            started_at=now - timedelta(hours=1), resolved_at=now,
        ))
        session.commit()

    return TestClient(app)


def test_health(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_list_devices(client: TestClient) -> None:
    resp = client.get("/devices")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    ids = {d["device_id"] for d in body}
    assert ids == {"router-01", "router-02"}


def test_get_device_by_id(client: TestClient) -> None:
    resp = client.get("/devices/router-01")
    assert resp.status_code == 200
    assert resp.json()["reachable"] is True


def test_get_device_not_found_returns_404(client: TestClient) -> None:
    resp = client.get("/devices/does-not-exist")
    assert resp.status_code == 404


def test_list_metrics_unfiltered(client: TestClient) -> None:
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert len(resp.json()) == 4


def test_list_metrics_filtered_by_device(client: TestClient) -> None:
    resp = client.get("/metrics", params={"device_id": "router-01", "metric_type": "bandwidth_in"})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    assert all(m["metric_type"] == "bandwidth_in" for m in body)


def test_list_metrics_filtered_by_time_range(client: TestClient) -> None:
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=30)
    resp = client.get("/metrics", params={"device_id": "router-01", "start": cutoff.isoformat()})
    body = resp.json()
    # the 2-hour-old bandwidth_in sample should be excluded; SQLite stores
    # naive datetimes, so compare naive-to-naive here.
    cutoff_naive = cutoff.replace(tzinfo=None)
    assert len(body) == 3
    assert all(datetime.fromisoformat(m["timestamp"]) >= cutoff_naive for m in body)


def test_metrics_for_device_endpoint(client: TestClient) -> None:
    resp = client.get("/metrics/router-01")
    assert resp.status_code == 200
    assert all(m["device_id"] == "router-01" for m in resp.json())


def test_bandwidth_endpoint_only_returns_bandwidth_metric_types(client: TestClient) -> None:
    resp = client.get("/metrics/router-01/bandwidth")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) >= 1
    assert all(m["metric_type"] in ("bandwidth_in", "bandwidth_out", "utilization_in", "utilization_out") for m in body)


def test_latency_endpoint_only_returns_latency_metric_types(client: TestClient) -> None:
    resp = client.get("/metrics/router-01/latency")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["metric_type"] == "latency_avg_ms"


def test_list_alerts(client: TestClient) -> None:
    resp = client.get("/alerts")
    assert resp.status_code == 200
    assert len(resp.json()) == 2


def test_active_alerts_only(client: TestClient) -> None:
    resp = client.get("/alerts/active")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["alert_type"] == "HIGH_BANDWIDTH"
    assert body[0]["status"] == "ACTIVE"


def test_export_csv_headers_and_content(client: TestClient) -> None:
    resp = client.get("/metrics/export", params={"device_id": "router-01"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert "attachment" in resp.headers["content-disposition"]

    lines = resp.text.strip().splitlines()
    header = lines[0].split(",")
    assert header == ["timestamp", "device_id", "device_ip", "interface", "metric_type", "metric_value", "unit"]
    # 4 seeded metrics for router-01
    assert len(lines) == 1 + 4


def test_export_csv_respects_metric_type_filter(client: TestClient) -> None:
    resp = client.get("/metrics/export", params={"device_id": "router-01", "metric_type": "latency_avg_ms"})
    lines = resp.text.strip().splitlines()
    assert len(lines) == 1 + 1  # header + one matching row
    assert "latency_avg_ms" in lines[1]
