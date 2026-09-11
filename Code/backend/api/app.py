"""
FastAPI application factory.

create_app() takes an explicit session_factory so tests can pass an
in-memory sqlite factory (backend.database.engine.create_session_factory
("sqlite:///:memory:")) without needing a real PostgreSQL instance -
the same pattern used throughout this project (mock SNMP transport,
fake gRPC stub, sqlite-backed service tests).
"""

from __future__ import annotations

import logging

from fastapi import FastAPI

from backend.api.alerts_router import router as alerts_router
from backend.api.devices_router import router as devices_router
from backend.api.health_router import router as health_router
from backend.api.metrics_router import router as metrics_router

logger = logging.getLogger(__name__)


def create_app(session_factory) -> FastAPI:
    app = FastAPI(
        title="Network Telemetry Backend",
        description=(
            "REST API for the Network Telemetry & Real-Time Network "
            "Performance Monitoring System. Conceptually aligned with "
            "RFC 9232 - see README for the full mapping."
        ),
        version="1.0.0",
    )
    app.state.session_factory = session_factory

    app.include_router(health_router)
    app.include_router(devices_router)
    app.include_router(metrics_router)
    app.include_router(alerts_router)

    _maybe_instrument_opentelemetry(app)

    return app


def _maybe_instrument_opentelemetry(app: FastAPI) -> None:
    """Instrument FastAPI with OpenTelemetry if the optional dependency is
    installed and OTEL_EXPORTER_OTLP_ENDPOINT is configured. Never fatal:
    this is application observability, not core functionality - its
    absence must not prevent the backend from starting.
    """
    import os

    if not os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        logger.info("OTEL_EXPORTER_OTLP_ENDPOINT not set - skipping OpenTelemetry instrumentation")
        return

    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app)
        logger.info("OpenTelemetry FastAPI instrumentation enabled")
    except ImportError:
        logger.warning("opentelemetry-instrumentation-fastapi not installed - skipping instrumentation")
