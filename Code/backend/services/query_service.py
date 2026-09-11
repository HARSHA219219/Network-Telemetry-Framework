"""
Service layer between FastAPI routes and the repository layer.

    API router -> service layer (this file) -> repository/database layer

Holds the small amount of "business" logic the API needs: which raw
metric_type values count as "bandwidth" vs "latency" for the two
convenience endpoints, and CSV row formatting for export.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterator
from datetime import datetime

from backend.api.repository import MetricRepository
from backend.models.orm import TelemetryMetric

BANDWIDTH_METRIC_TYPES = {"bandwidth_in", "bandwidth_out", "utilization_in", "utilization_out"}
LATENCY_METRIC_TYPES = {"latency_avg_ms", "latency_min_ms", "latency_max_ms", "packet_loss_percent"}

_EXPORT_BATCH_SIZE = 500


def get_bandwidth_metrics(
    repo: MetricRepository,
    device_id: str,
    interface_name: str | None,
    start: datetime | None,
    end: datetime | None,
    limit: int,
) -> list[TelemetryMetric]:
    # No single-metric_type filter here since bandwidth spans 4 metric
    # types (in/out x bandwidth/utilization) - filter in Python instead,
    # after using the repository's indexed device_id/timestamp filtering
    # to keep the DB-side scan small.
    rows = repo.query(device_id=device_id, interface_name=interface_name, start=start, end=end, limit=limit * 4)
    return [r for r in rows if r.metric_type in BANDWIDTH_METRIC_TYPES][:limit]


def get_latency_metrics(
    repo: MetricRepository,
    device_id: str,
    start: datetime | None,
    end: datetime | None,
    limit: int,
) -> list[TelemetryMetric]:
    rows = repo.query(device_id=device_id, start=start, end=end, limit=limit * 4)
    return [r for r in rows if r.metric_type in LATENCY_METRIC_TYPES][:limit]


def stream_metrics_csv(
    session_factory,
    device_id: str | None,
    interface_name: str | None,
    metric_type: str | None,
    start: datetime | None,
    end: datetime | None,
    max_rows: int = 50_000,
) -> Iterator[str]:
    """Yield CSV text in chunks, querying the database in batches rather
    than loading the full result set into memory at once.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["timestamp", "device_id", "device_ip", "interface", "metric_type", "metric_value", "unit"])
    yield buffer.getvalue()

    offset = 0
    rows_emitted = 0
    with session_factory() as session:
        repo = MetricRepository(session)
        while rows_emitted < max_rows:
            batch = repo.query(
                device_id=device_id,
                interface_name=interface_name,
                metric_type=metric_type,
                start=start,
                end=end,
                limit=_EXPORT_BATCH_SIZE,
                offset=offset,
            )
            if not batch:
                break

            buffer = io.StringIO()
            writer = csv.writer(buffer)
            for row in batch:
                writer.writerow([
                    row.timestamp.isoformat(),
                    row.device_id,
                    row.device_ip,
                    row.interface_name or "",
                    row.metric_type,
                    row.metric_value,
                    row.unit,
                ])
            yield buffer.getvalue()

            rows_emitted += len(batch)
            offset += _EXPORT_BATCH_SIZE
            if len(batch) < _EXPORT_BATCH_SIZE:
                break
