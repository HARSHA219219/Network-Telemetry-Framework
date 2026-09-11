from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class HealthOut(BaseModel):
    status: str
    service: str = "network-telemetry-backend"


class DeviceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    device_id: str
    device_ip: str
    kind: str | None = None
    reachable: bool
    last_seen: datetime | None = None


class MetricOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    device_id: str
    device_ip: str
    interface_name: str | None = None
    metric_type: str
    metric_value: float
    unit: str
    timestamp: datetime


class AlertOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    device_id: str
    interface_name: str | None = None
    metric_type: str | None = None
    alert_type: str
    severity: str
    metric_value: float
    threshold: float
    status: str
    started_at: datetime
    resolved_at: datetime | None = None
