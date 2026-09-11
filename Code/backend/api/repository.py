"""
Repository layer: all SQLAlchemy query logic for the REST API lives
here, kept out of route functions per the layering requirement:

    API router -> service layer -> repository/database layer

Nothing here knows about FastAPI, HTTP, or Pydantic - it takes/returns
plain ORM objects and primitives, which keeps it directly unit-testable
against the same sqlite-in-memory pattern used elsewhere in the project.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models.orm import Alert, Device, TelemetryMetric


class DeviceRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_all(self) -> list[Device]:
        return list(self._session.execute(select(Device).order_by(Device.device_id)).scalars().all())

    def get_by_device_id(self, device_id: str) -> Device | None:
        return self._session.execute(
            select(Device).where(Device.device_id == device_id)
        ).scalars().first()


class MetricRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def query(
        self,
        device_id: str | None = None,
        interface_name: str | None = None,
        metric_type: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 1000,
        offset: int = 0,
    ) -> list[TelemetryMetric]:
        stmt = select(TelemetryMetric)
        if device_id is not None:
            stmt = stmt.where(TelemetryMetric.device_id == device_id)
        if interface_name is not None:
            stmt = stmt.where(TelemetryMetric.interface_name == interface_name)
        if metric_type is not None:
            stmt = stmt.where(TelemetryMetric.metric_type == metric_type)
        if start is not None:
            stmt = stmt.where(TelemetryMetric.timestamp >= start)
        if end is not None:
            stmt = stmt.where(TelemetryMetric.timestamp <= end)

        # Index-friendly ordering: (device_id, metric_type, timestamp) and
        # (device_id, timestamp) composite indexes exist on this table
        # (see backend/models/orm.py), so filtering + ordering by timestamp
        # avoids a full table scan for the common query shapes above.
        stmt = stmt.order_by(TelemetryMetric.timestamp.desc()).offset(offset).limit(limit)
        return list(self._session.execute(stmt).scalars().all())


class AlertRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_all(self, limit: int = 500) -> list[Alert]:
        stmt = select(Alert).order_by(Alert.started_at.desc()).limit(limit)
        return list(self._session.execute(stmt).scalars().all())

    def list_active(self) -> list[Alert]:
        stmt = select(Alert).where(Alert.status == "ACTIVE").order_by(Alert.started_at.desc())
        return list(self._session.execute(stmt).scalars().all())
