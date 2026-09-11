from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from backend.api.dependencies import get_session
from backend.api.repository import MetricRepository
from backend.models.schemas import MetricOut
from backend.services.query_service import get_bandwidth_metrics, get_latency_metrics, stream_metrics_csv

router = APIRouter(tags=["metrics"])


@router.get("/metrics", response_model=list[MetricOut])
def list_metrics(
    device_id: str | None = Query(default=None),
    interface: str | None = Query(default=None),
    metric_type: str | None = Query(default=None),
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    limit: int = Query(default=500, ge=1, le=5000),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
) -> list[MetricOut]:
    rows = MetricRepository(session).query(
        device_id=device_id, interface_name=interface, metric_type=metric_type,
        start=start, end=end, limit=limit, offset=offset,
    )
    return [MetricOut.model_validate(r) for r in rows]


@router.get("/metrics/export")
def export_metrics_csv(
    device_id: str | None = Query(default=None),
    interface: str | None = Query(default=None),
    metric_type: str | None = Query(default=None),
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    request: Request = None,  # type: ignore[assignment]
) -> StreamingResponse:
    session_factory = request.app.state.session_factory
    csv_iterator = stream_metrics_csv(
        session_factory, device_id=device_id, interface_name=interface,
        metric_type=metric_type, start=start, end=end,
    )
    return StreamingResponse(
        csv_iterator,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="telemetry_export.csv"'},
    )


@router.get("/metrics/{device_id}", response_model=list[MetricOut])
def list_metrics_for_device(
    device_id: str,
    interface: str | None = Query(default=None),
    metric_type: str | None = Query(default=None),
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    limit: int = Query(default=500, ge=1, le=5000),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
) -> list[MetricOut]:
    rows = MetricRepository(session).query(
        device_id=device_id, interface_name=interface, metric_type=metric_type,
        start=start, end=end, limit=limit, offset=offset,
    )
    return [MetricOut.model_validate(r) for r in rows]


@router.get("/metrics/{device_id}/bandwidth", response_model=list[MetricOut])
def get_device_bandwidth(
    device_id: str,
    interface: str | None = Query(default=None),
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    limit: int = Query(default=500, ge=1, le=5000),
    session: Session = Depends(get_session),
) -> list[MetricOut]:
    rows = get_bandwidth_metrics(MetricRepository(session), device_id, interface, start, end, limit)
    return [MetricOut.model_validate(r) for r in rows]


@router.get("/metrics/{device_id}/latency", response_model=list[MetricOut])
def get_device_latency(
    device_id: str,
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    limit: int = Query(default=500, ge=1, le=5000),
    session: Session = Depends(get_session),
) -> list[MetricOut]:
    rows = get_latency_metrics(MetricRepository(session), device_id, start, end, limit)
    return [MetricOut.model_validate(r) for r in rows]
