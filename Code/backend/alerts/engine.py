"""
Threshold-based alert engine.

Critical requirement: deduplication. If a metric stays above threshold
for N consecutive polls, there must be exactly ONE ACTIVE alert, not N.
This is implemented by always checking for an existing ACTIVE alert for
the same (device_id, interface_name, alert_type) key before creating a
new one - a breach while one is already ACTIVE is a no-op, not a new row.

Recovery: when the metric next comes in below threshold (or the device
becomes reachable again), any matching ACTIVE alert is transitioned to
RESOLVED with a resolved_at timestamp. There is deliberately no
hysteresis/debounce band in this version (documented as a future
enhancement) - the mapping metric_type -> alert_type is a simple,
explicit table, which keeps behavior deterministic and easy to test.
"""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models.orm import Alert

logger = logging.getLogger(__name__)

ALERT_TYPE_HIGH_BANDWIDTH = "HIGH_BANDWIDTH"
ALERT_TYPE_HIGH_LATENCY = "HIGH_LATENCY"
ALERT_TYPE_PACKET_LOSS = "PACKET_LOSS"
ALERT_TYPE_DEVICE_DOWN = "DEVICE_DOWN"

_METRIC_TYPE_TO_ALERT_TYPE = {
    "utilization_in": ALERT_TYPE_HIGH_BANDWIDTH,
    "utilization_out": ALERT_TYPE_HIGH_BANDWIDTH,
    "latency_avg_ms": ALERT_TYPE_HIGH_LATENCY,
    "packet_loss_percent": ALERT_TYPE_PACKET_LOSS,
}

_ALERT_TYPE_SEVERITY = {
    ALERT_TYPE_HIGH_BANDWIDTH: "WARNING",
    ALERT_TYPE_HIGH_LATENCY: "WARNING",
    ALERT_TYPE_PACKET_LOSS: "WARNING",
    ALERT_TYPE_DEVICE_DOWN: "CRITICAL",
}


class AlertEngine:
    def __init__(self, session_factory, thresholds: dict) -> None:
        self._session_factory = session_factory
        self.thresholds = thresholds

    def evaluate_record(
        self,
        device_id: str,
        interface_name: str | None,
        metric_type: str,
        value: float,
        timestamp: datetime,
    ) -> Alert | None:
        """Evaluate one telemetry record against thresholds.

        Returns the Alert row that was created or resolved, or None if
        nothing changed (includes the dedup no-op case: already-ACTIVE
        alert with an ongoing breach).
        """
        if metric_type == "device_reachable":
            return self._evaluate_device_reachable(device_id, value, timestamp)

        alert_type = _METRIC_TYPE_TO_ALERT_TYPE.get(metric_type)
        if alert_type is None:
            return None  # not an alert-relevant metric type

        threshold = self._threshold_for(alert_type)
        if threshold is None:
            return None

        return self._evaluate_threshold(device_id, interface_name, alert_type, value, threshold, timestamp, metric_type)

    def _threshold_for(self, alert_type: str) -> float | None:
        key = {
            ALERT_TYPE_HIGH_BANDWIDTH: "bandwidth_utilization_percent",
            ALERT_TYPE_HIGH_LATENCY: "latency_ms",
            ALERT_TYPE_PACKET_LOSS: "packet_loss_percent",
        }.get(alert_type)
        return self.thresholds.get(key) if key else None

    def _evaluate_threshold(
        self,
        device_id: str,
        interface_name: str | None,
        alert_type: str,
        value: float,
        threshold: float,
        timestamp: datetime,
        metric_type: str,
    ) -> Alert | None:
        with self._session_factory() as session:
            existing = self._find_active(session, device_id, interface_name, alert_type, metric_type)
            breaching = value > threshold

            if breaching:
                if existing is not None:
                    return None  # dedup: condition ongoing, no new alert row
                alert = Alert(
                    device_id=device_id, interface_name=interface_name, alert_type=alert_type,
                    metric_type=metric_type,
                    severity=_ALERT_TYPE_SEVERITY[alert_type], metric_value=value, threshold=threshold,
                    status="ACTIVE", started_at=timestamp,
                )
                session.add(alert)
                session.commit()
                session.refresh(alert)
                logger.info("ALERT %s ACTIVE for %s (value=%.2f > threshold=%.2f)", alert_type, device_id, value, threshold)
                return alert

            if existing is not None:
                existing.status = "RESOLVED"
                existing.resolved_at = timestamp
                session.commit()
                session.refresh(existing)
                logger.info("ALERT %s RESOLVED for %s (value=%.2f <= threshold=%.2f)", alert_type, device_id, value, threshold)
                return existing

            return None

    def _evaluate_device_reachable(self, device_id: str, value: float, timestamp: datetime) -> Alert | None:
        with self._session_factory() as session:
            existing = self._find_active(session, device_id, None, ALERT_TYPE_DEVICE_DOWN, "device_reachable")
            down = value == 0.0

            if down:
                if existing is not None:
                    return None  # dedup
                alert = Alert(
                    device_id=device_id, interface_name=None, alert_type=ALERT_TYPE_DEVICE_DOWN,
                    metric_type="device_reachable",
                    severity=_ALERT_TYPE_SEVERITY[ALERT_TYPE_DEVICE_DOWN], metric_value=0.0, threshold=1.0,
                    status="ACTIVE", started_at=timestamp,
                )
                session.add(alert)
                session.commit()
                session.refresh(alert)
                logger.warning("ALERT DEVICE_DOWN ACTIVE for %s", device_id)
                return alert

            if existing is not None:
                existing.status = "RESOLVED"
                existing.resolved_at = timestamp
                session.commit()
                session.refresh(existing)
                logger.info("ALERT DEVICE_DOWN RESOLVED for %s (device reachable again)", device_id)
                return existing

            return None

    @staticmethod
    def _find_active(
        session: Session, device_id: str, interface_name: str | None, alert_type: str, metric_type: str
    ) -> Alert | None:
        stmt = select(Alert).where(
            Alert.device_id == device_id,
            Alert.alert_type == alert_type,
            Alert.metric_type == metric_type,
            Alert.status == "ACTIVE",
        )
        stmt = stmt.where(Alert.interface_name.is_(None)) if interface_name is None else stmt.where(
            Alert.interface_name == interface_name
        )
        return session.execute(stmt).scalars().first()
