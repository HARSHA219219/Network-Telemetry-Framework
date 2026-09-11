"""
Backend entrypoint. Runs two servers in one process, sharing the same
session_factory/alert_engine/ingestion_service instances:

  - the gRPC TelemetryIngestion server (collector -> backend transport)
  - the FastAPI REST API (external/historical access, Grafana's Postgres
    datasource reads the same DB directly, not through this API)

    gRPC server -> TelemetryIngestionService -> DB + AlertEngine
    FastAPI     -> repository layer          -> DB (read-only)
"""

from __future__ import annotations

import asyncio
import logging
import os

import uvicorn

from backend.alerts.config import load_thresholds
from backend.alerts.engine import AlertEngine
from backend.api.app import create_app
from backend.database.engine import create_session_factory
from backend.grpc_server.server import serve
from backend.services.ingestion_service import TelemetryIngestionService
from backend.telemetry.otel_setup import maybe_instrument_grpc_server, setup_opentelemetry

logger = logging.getLogger(__name__)


async def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # Application observability (this app's own health), independent of
    # the network telemetry the app collects - see backend/telemetry/otel_setup.py.
    setup_opentelemetry()
    maybe_instrument_grpc_server()

    session_factory = create_session_factory()  # PostgreSQL via env vars; see get_database_url()

    thresholds_path = os.environ.get("THRESHOLDS_CONFIG_PATH", "config/thresholds.yaml")
    thresholds = load_thresholds(thresholds_path)
    alert_engine = AlertEngine(session_factory, thresholds)
    ingestion_service = TelemetryIngestionService(session_factory, alert_engine)

    grpc_port = int(os.environ.get("BACKEND_GRPC_PORT", "50051"))
    grpc_server = await serve(grpc_port, ingestion_service)

    app = create_app(session_factory)
    http_port = int(os.environ.get("BACKEND_HTTP_PORT", "8000"))
    uvicorn_config = uvicorn.Config(
        app, host="0.0.0.0", port=http_port,
        log_level=os.environ.get("LOG_LEVEL", "info").lower(),
    )
    http_server = uvicorn.Server(uvicorn_config)

    logger.info("Backend starting: REST on :%d, gRPC on :%d", http_port, grpc_port)
    try:
        await http_server.serve()
    finally:
        await grpc_server.stop(grace=2.0)


if __name__ == "__main__":
    asyncio.run(main())
