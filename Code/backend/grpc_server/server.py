"""gRPC server bootstrap - wires the servicer into a running grpc.aio server."""

from __future__ import annotations

import logging

import grpc

from backend.grpc_server.servicer import TelemetryIngestionServicer
from backend.services.ingestion_service import TelemetryIngestionService
from common import telemetry_pb2_grpc

logger = logging.getLogger(__name__)


async def serve(port: int, ingestion_service: TelemetryIngestionService) -> grpc.aio.Server:
    server = grpc.aio.server()
    telemetry_pb2_grpc.add_TelemetryIngestionServicer_to_server(
        TelemetryIngestionServicer(ingestion_service), server
    )
    server.add_insecure_port(f"[::]:{port}")
    await server.start()
    logger.info("gRPC telemetry ingestion server listening on port %d", port)
    return server
