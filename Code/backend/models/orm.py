"""
SQLAlchemy models: devices, interfaces, telemetry_metrics, alerts.

Kept deliberately simple for an academic project - no over-normalization.
Indexes target the query patterns the REST API and Grafana actually use:
lookups/filters by device_id, timestamp range, metric_type, and
interface_name.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database.engine import Base


class Device(Base):
    __tablename__ = "devices"

    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    device_ip: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    reachable: Mapped[bool] = mapped_column(Boolean, default=True)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    interfaces: Mapped[list["Interface"]] = relationship(back_populates="device", cascade="all, delete-orphan")


class Interface(Base):
    __tablename__ = "interfaces"

    id: Mapped[int] = mapped_column(primary_key=True)
    device_pk: Mapped[int] = mapped_column(ForeignKey("devices.id"), index=True)
    if_index: Mapped[int] = mapped_column(Integer)
    if_name: Mapped[str] = mapped_column(String(128), index=True)

    device: Mapped["Device"] = relationship(back_populates="interfaces")

    __table_args__ = (UniqueConstraint("device_pk", "if_index", name="uq_interface_device_index"),)


class TelemetryMetric(Base):
    __tablename__ = "telemetry_metrics"

    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[str] = mapped_column(String(128), index=True)
    device_ip: Mapped[str] = mapped_column(String(64))
    interface_name: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    metric_type: Mapped[str] = mapped_column(String(64), index=True)
    metric_value: Mapped[float] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(16))
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

    __table_args__ = (
        Index("ix_metrics_device_time", "device_id", "timestamp"),
        Index("ix_metrics_device_metric_time", "device_id", "metric_type", "timestamp"),
    )


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[str] = mapped_column(String(128), index=True)
    interface_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # The specific metric_type that triggered this alert (e.g. "utilization_in"
    # vs "utilization_out"). Included in the dedup key alongside alert_type
    # because a single interface can independently breach on inbound and
    # outbound utilization at the same time - without this, evaluating one
    # direction's non-breach would incorrectly resolve the other direction's
    # active alert (both map to the same HIGH_BANDWIDTH alert_type).
    metric_type: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    alert_type: Mapped[str] = mapped_column(String(32), index=True)   # HIGH_BANDWIDTH | HIGH_LATENCY | PACKET_LOSS | DEVICE_DOWN
    severity: Mapped[str] = mapped_column(String(16), default="WARNING")
    metric_value: Mapped[float] = mapped_column(Float)
    threshold: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE", index=True)  # ACTIVE | RESOLVED
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_alerts_device_type_status", "device_id", "alert_type", "status"),
    )
