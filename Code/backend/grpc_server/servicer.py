"""
gRPC servicer. Deliberately thin: converts TelemetryBatch protobuf ->
plain dicts, delegates everything else to TelemetryIngestionService.

    gRPC servicer -> TelemetryIngestionService -> DB + AlertEngine

Business logic (validation, upserts, alerting) lives in the service
layer, not here, per the project's layering requirement.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from common import telemetry_pb2, telemetry_pb2_grpc
from backend.services.ingestion_service import TelemetryIngestionService

logger = logging.getLogger(__name__)


def _record_to_dict(record: telemetry_pb2.TelemetryRecord) -> dict:
    return {
        "device_id": record.device_id,
        "device_ip": record.device_ip,
        "timestamp": datetime.fromtimestamp(record.timestamp_unix_ms / 1000, tz=timezone.utc),
        "metric_type": record.metric_type,
        "metric_value": record.metric_value,
        "unit": record.unit,
        "interface_name": record.interface_name or None,
        "interface_index": record.interface_index,
    }


class TelemetryIngestionServicer(telemetry_pb2_grpc.TelemetryIngestionServicer):
    def __init__(self, ingestion_service: TelemetryIngestionService) -> None:
        self._service = ingestion_service

    async def SendTelemetryBatch(self, request, context):
        try:
            records = [_record_to_dict(r) for r in request.records]
            stored = self._service.ingest_records(records)
            return telemetry_pb2.SendTelemetryBatchResponse(
                accepted=True, records_stored=stored,
                message=f"stored {stored}/{len(records)} records from collector '{request.collector_id}'",
            )
        except Exception as exc:  # noqa: BLE001 - must never crash the gRPC server on one bad batch
            logger.exception("Failed to ingest telemetry batch from collector '%s'", request.collector_id)
            return telemetry_pb2.SendTelemetryBatchResponse(accepted=False, records_stored=0, message=str(exc))
