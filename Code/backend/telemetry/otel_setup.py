"""
OpenTelemetry setup for THIS APPLICATION's own observability (backend
request latency, gRPC call health) - not to be confused with the
network telemetry (bandwidth/latency/etc.) this project collects via
SNMP/ICMP, which flows through the gRPC/Postgres/Grafana pipeline
instead.

Entirely optional: if OTEL_EXPORTER_OTLP_ENDPOINT is unset, or the
opentelemetry packages aren't installed, this is a silent no-op so the
backend still starts cleanly without an observability backend running.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_configured = False


def setup_opentelemetry(service_name: str = "network-telemetry-backend") -> bool:
    """Configure a global TracerProvider exporting to OTEL_EXPORTER_OTLP_ENDPOINT.

    Returns True if tracing was configured, False if skipped (missing
    config or missing optional dependency).
    """
    global _configured
    if _configured:
        return True

    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not endpoint:
        return False

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import SERVICE_NAME, Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        logger.warning("OpenTelemetry SDK packages not installed - skipping tracing setup")
        return False

    resource = Resource.create({SERVICE_NAME: service_name})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, insecure=True)))
    trace.set_tracer_provider(provider)

    _configured = True
    logger.info("OpenTelemetry tracing configured: service=%s endpoint=%s", service_name, endpoint)
    return True


def maybe_instrument_grpc_server() -> None:
    """Instrument the grpc.aio server side, if configured and available."""
    if not os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return
    try:
        from opentelemetry.instrumentation.grpc import GrpcInstrumentorServer

        GrpcInstrumentorServer().instrument()
        logger.info("OpenTelemetry gRPC server instrumentation enabled")
    except ImportError:
        logger.warning("opentelemetry-instrumentation-grpc not installed - skipping gRPC instrumentation")
