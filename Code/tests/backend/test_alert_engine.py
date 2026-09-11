from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from backend.alerts.engine import AlertEngine
from backend.database.engine import create_session_factory
from backend.models.orm import Alert

THRESHOLDS = {"bandwidth_utilization_percent": 80, "latency_ms": 100, "packet_loss_percent": 5}


@pytest.fixture
def session_factory():
    return create_session_factory("sqlite:///:memory:")


@pytest.fixture
def engine(session_factory):
    return AlertEngine(session_factory, THRESHOLDS)


def _t(seconds_offset: int = 0) -> datetime:
    return datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=seconds_offset)


def _active_alerts(session_factory) -> list[Alert]:
    with session_factory() as session:
        return list(session.execute(select(Alert).where(Alert.status == "ACTIVE")).scalars())


# --- threshold breach ---

def test_bandwidth_breach_creates_active_alert(engine, session_factory) -> None:
    alert = engine.evaluate_record("router-01", "eth0", "utilization_in", 91.0, _t())

    assert alert is not None
    assert alert.status == "ACTIVE"
    assert alert.alert_type == "HIGH_BANDWIDTH"
    assert len(_active_alerts(session_factory)) == 1


def test_bandwidth_below_threshold_creates_no_alert(engine, session_factory) -> None:
    alert = engine.evaluate_record("router-01", "eth0", "utilization_in", 50.0, _t())

    assert alert is None
    assert _active_alerts(session_factory) == []


# --- CRITICAL: deduplication ---

def test_sustained_breach_over_20_polls_produces_exactly_one_active_alert(engine, session_factory) -> None:
    for i in range(20):
        engine.evaluate_record("router-01", "eth0", "utilization_in", 91.0, _t(i * 10))

    active = _active_alerts(session_factory)
    assert len(active) == 1
    assert active[0].alert_type == "HIGH_BANDWIDTH"


def test_dedup_is_scoped_per_device_interface_alert_type(engine, session_factory) -> None:
    for i in range(5):
        engine.evaluate_record("router-01", "eth0", "utilization_in", 91.0, _t(i))
    for i in range(5):
        engine.evaluate_record("router-01", "eth1", "utilization_in", 91.0, _t(i))  # different interface
    for i in range(5):
        engine.evaluate_record("router-02", "eth0", "utilization_in", 91.0, _t(i))  # different device

    active = _active_alerts(session_factory)
    assert len(active) == 3  # one per distinct (device, interface) pair


# --- recovery ---

def test_metric_returning_below_threshold_resolves_alert(engine, session_factory) -> None:
    engine.evaluate_record("router-01", "eth0", "utilization_in", 91.0, _t(0))
    assert len(_active_alerts(session_factory)) == 1

    resolved = engine.evaluate_record("router-01", "eth0", "utilization_in", 40.0, _t(10))

    assert resolved is not None
    assert resolved.status == "RESOLVED"
    assert resolved.resolved_at is not None
    assert _active_alerts(session_factory) == []


def test_new_breach_after_recovery_creates_a_new_alert(engine, session_factory) -> None:
    engine.evaluate_record("router-01", "eth0", "utilization_in", 91.0, _t(0))
    engine.evaluate_record("router-01", "eth0", "utilization_in", 40.0, _t(10))  # resolves
    second_alert = engine.evaluate_record("router-01", "eth0", "utilization_in", 95.0, _t(20))  # breaches again

    assert second_alert is not None
    assert second_alert.status == "ACTIVE"
    assert len(_active_alerts(session_factory)) == 1


# --- latency / packet loss ---


def test_in_and_out_utilization_breaches_are_tracked_independently(engine, session_factory) -> None:
    """Regression test: utilization_in and utilization_out both map to
    HIGH_BANDWIDTH for the same interface. Before metric_type was part of
    the dedup key, evaluating a non-breaching direction (e.g. out=50%)
    right after a breaching one (in=91%) on the same interface would
    incorrectly resolve the alert the first record had just created.
    """
    in_alert = engine.evaluate_record("router-01", "eth0", "utilization_in", 91.0, _t(0))
    out_result = engine.evaluate_record("router-01", "eth0", "utilization_out", 50.0, _t(0))

    assert in_alert is not None
    assert in_alert.status == "ACTIVE"
    assert out_result is None  # no alert created for the non-breaching direction

    active = _active_alerts(session_factory)
    assert len(active) == 1, "the in-direction alert must still be ACTIVE, not resolved by the out-direction check"
    assert active[0].metric_type == "utilization_in"


def test_high_latency_breach_and_recovery(engine, session_factory) -> None:
    alert = engine.evaluate_record("router-01", None, "latency_avg_ms", 150.0, _t(0))
    assert alert.alert_type == "HIGH_LATENCY"

    resolved = engine.evaluate_record("router-01", None, "latency_avg_ms", 20.0, _t(10))
    assert resolved.status == "RESOLVED"


def test_packet_loss_breach(engine, session_factory) -> None:
    alert = engine.evaluate_record("router-01", None, "packet_loss_percent", 15.0, _t(0))
    assert alert.alert_type == "PACKET_LOSS"


# --- device down / recovery ---

def test_device_unreachable_creates_device_down_alert(engine, session_factory) -> None:
    alert = engine.evaluate_record("router-01", None, "device_reachable", 0.0, _t(0))

    assert alert is not None
    assert alert.alert_type == "DEVICE_DOWN"
    assert alert.severity == "CRITICAL"


def test_device_down_deduplicates_across_repeated_polls(engine, session_factory) -> None:
    for i in range(10):
        engine.evaluate_record("router-01", None, "device_reachable", 0.0, _t(i))

    active = [a for a in _active_alerts(session_factory) if a.alert_type == "DEVICE_DOWN"]
    assert len(active) == 1


def test_device_recovery_resolves_device_down_alert(engine, session_factory) -> None:
    engine.evaluate_record("router-01", None, "device_reachable", 0.0, _t(0))
    resolved = engine.evaluate_record("router-01", None, "device_reachable", 1.0, _t(30))

    assert resolved.status == "RESOLVED"
    assert _active_alerts(session_factory) == []
