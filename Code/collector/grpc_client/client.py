"""
Collector-side gRPC client. Sends TelemetryBatch messages to the backend
with retry + exponential backoff, and never raises out of send_batch() -
a temporarily unavailable backend must not crash the collector or stop
it from polling other devices / retrying on the next cycle.
"""

from __future__ import annotations

import asyncio
import logging

import grpc

from common import telemetry_pb2, telemetry_pb2_grpc

logger = logging.getLogger(__name__)


class TelemetryGrpcClient:
    def __init__(
        self,
        host: str,
        port: int,
        timeout_seconds: float = 5.0,
        max_retries: int = 3,
        retry_backoff_seconds: float = 2.0,
    ) -> None:
        self._host = host
        self._port = port
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        self._retry_backoff_seconds = retry_backoff_seconds
        self._channel: grpc.aio.Channel | None = None
        self._stub: telemetry_pb2_grpc.TelemetryIngestionStub | None = None

    def _ensure_channel(self) -> None:
        if self._channel is None:
            self._channel = grpc.aio.insecure_channel(f"{self._host}:{self._port}")
            self._stub = telemetry_pb2_grpc.TelemetryIngestionStub(self._channel)

    async def send_batch(
        self, batch: telemetry_pb2.TelemetryBatch
    ) -> telemetry_pb2.SendTelemetryBatchResponse | None:
        """Send a batch, retrying on failure. Returns None (never raises)
        if the backend is unavailable after all retries are exhausted -
        the caller (main collector loop) should treat that as "try again
        next poll cycle", not as a fatal error.
        """
        self._ensure_channel()
        assert self._stub is not None

        delay = self._retry_backoff_seconds
        for attempt in range(1, self._max_retries + 2):  # +1 initial try, +1 for range inclusivity
            try:
                response = await self._stub.SendTelemetryBatch(batch, timeout=self._timeout_seconds)
                if attempt > 1:
                    logger.info("gRPC send succeeded on attempt %d", attempt)
                return response
            except grpc.aio.AioRpcError as exc:
                if attempt > self._max_retries:
                    logger.error(
                        "Backend unavailable after %d attempts; dropping this batch (%d records): %s",
                        self._max_retries, len(batch.records), exc.details() if hasattr(exc, "details") else exc,
                    )
                    return None
                logger.warning(
                    "gRPC send attempt %d/%d failed (%s); retrying in %.1fs",
                    attempt, self._max_retries, exc.code() if hasattr(exc, "code") else exc, delay,
                )
                await asyncio.sleep(delay)
                delay *= 2  # exponential backoff
        return None

    async def close(self) -> None:
        if self._channel is not None:
            await self._channel.close()
            self._channel = None
