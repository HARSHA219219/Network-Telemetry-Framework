"""
Business logic for processing incoming telemetry, kept separate from the
gRPC transport layer (backend/grpc_server/servicer.py) so it can be
tested directly and reused if another ingestion path is ever added.

Takes plain dicts, not protobuf messages, as input - the gRPC servicer
is responsible for that conversion - which keeps this service testable
without any gRPC/protobuf machinery.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from backend.alerts.engine import AlertEngine
from backend.models.orm import Device, Interface, TelemetryMetric

logger = logging.getLogger(__name__)


class TelemetryIngestionService:
    def __init__(self, session_factory, alert_engine: AlertEngine) -> None:
        self._session_factory = session_factory
        self._alert_engine = alert_engine

    def ingest_records(self, records: list[dict[str, Any]]) -> int:
        """Validate, store, and alert-evaluate a batch of telemetry records.

        Returns the number of records actually stored (invalid records
        are skipped and logged, not fatal to the rest of the batch).
        """
        valid_records = [r for r in records if self._is_valid(r)]
        skipped = len(records) - len(valid_records)
        if skipped:
            logger.warning("Skipped %d invalid telemetry record(s) in batch", skipped)

        with self._session_factory() as session:
            for record in valid_records:
                device = self._get_or_create_device(session, record["device_id"], record["device_ip"])
                device.last_seen = record["timestamp"]
                if record["metric_type"] == "device_reachable":
                    device.reachable = record["metric_value"] != 0.0

                if record.get("interface_name"):
                    self._get_or_create_interface(
                        session, device, record.get("interface_index", -1), record["interface_name"]
                    )

                session.add(
                    TelemetryMetric(
                        device_id=record["device_id"],
                        device_ip=record["device_ip"],
                        interface_name=record.get("interface_name") or None,
                        metric_type=record["metric_type"],
                        metric_value=record["metric_value"],
                        unit=record.get("unit", ""),
                        timestamp=record["timestamp"],
                    )
                )
            session.commit()

        for record in valid_records:
            self._alert_engine.evaluate_record(
                device_id=record["device_id"],
                interface_name=record.get("interface_name") or None,
                metric_type=record["metric_type"],
                value=record["metric_value"],
                timestamp=record["timestamp"],
            )

        return len(valid_records)

    @staticmethod
    def _is_valid(record: dict[str, Any]) -> bool:
        if not record.get("device_id"):
            return False
        if not record.get("metric_type"):
            return False
        if not isinstance(record.get("metric_value"), (int, float)):
            return False
        if not isinstance(record.get("timestamp"), datetime):
            return False
        return True

    @staticmethod
    def _get_or_create_device(session, device_id: str, device_ip: str) -> Device:
        device = session.execute(select(Device).where(Device.device_id == device_id)).scalars().first()
        if device is None:
            device = Device(device_id=device_id, device_ip=device_ip, reachable=True)
            session.add(device)
            session.flush()
        else:
            device.device_ip = device_ip
        return device

    @staticmethod
    def _get_or_create_interface(session, device: Device, if_index: int, if_name: str) -> Interface:
        iface = session.execute(
            select(Interface).where(Interface.device_pk == device.id, Interface.if_index == if_index)
        ).scalars().first()
        if iface is None:
            iface = Interface(device_pk=device.id, if_index=if_index, if_name=if_name)
            session.add(iface)
            session.flush()
        else:
            iface.if_name = if_name
        return iface


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
