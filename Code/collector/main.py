"""
Collector entrypoint.

Wires together, based on config/collector.yaml `mode`:
  - a MetricSource   (SNMPSource for real devices [PRIMARY], SimulatorSource for demo/dev [SECONDARY])
  - a LatencySource  (IcmpPingSource or SimulatedLatencySource, independently configurable)
  - the Phase 3 metric calculation engine (bandwidth/utilization from consecutive samples)
  - the gRPC client (Phase 6/7), sending one batch per device per poll round

Loop shape: poll -> calculate -> build batch -> send -> sleep -> repeat.
Every device is polled independently within a round; one device's
failure (SNMP timeout, ping failure, gRPC send failure) never stops the
others from being polled, per the project's error-handling requirement.
"""

from __future__ import annotations

import asyncio
import logging
import os

import yaml

from collector.config.device_config import load_devices
from collector.grpc_client.client import TelemetryGrpcClient
from collector.grpc_client.telemetry_builder import build_batch
from collector.metrics.interface_metrics import calculate_device_metrics
from collector.ping.base import LatencySource
from collector.ping.icmp_source import IcmpPingSource
from collector.simulator.latency_source import SimulatedLatencySource
from collector.simulator.scenario_controller import ScenarioController
from collector.simulator.simulator_source import SimulatorSource
from collector.snmp.snmp_source import SNMPSource
from collector.sources.base import DeviceConfig, MetricSource, RawDeviceSample

logger = logging.getLogger(__name__)


def load_collector_config(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def build_metric_source(config: dict, scenario_controller: ScenarioController) -> MetricSource:
    """mode: SNMP is the PRIMARY, real-world data source. mode: SIMULATION
    (the default) is the secondary source used for development/testing/
    demos when authorized physical device access isn't available.
    """
    mode = str(config.get("mode", "SIMULATION")).upper()
    if mode == "SNMP":
        return SNMPSource(
            timeout_seconds=config.get("snmp_timeout_seconds", 2.0),
            retries=config.get("snmp_retries", 1),
        )
    return SimulatorSource(controller=scenario_controller)


def build_latency_source(config: dict, scenario_controller: ScenarioController) -> LatencySource:
    latency_cfg = config.get("latency", {})
    mode = str(latency_cfg.get("mode", "SIMULATION")).upper()
    if mode == "ICMP":
        return IcmpPingSource(
            probes_per_measurement=latency_cfg.get("probes_per_measurement", 4),
            timeout_seconds=latency_cfg.get("timeout_seconds", 1.0),
        )
    return SimulatedLatencySource(controller=scenario_controller)


class CollectorApp:
    """Owns the poll loop. Kept independent of how it was constructed
    (see build_app_from_config) so it's directly testable with fakes.
    """

    def __init__(
        self,
        devices: list[DeviceConfig],
        metric_source: MetricSource,
        latency_source: LatencySource,
        grpc_client: TelemetryGrpcClient,
        collection_interval: float,
        collector_id: str = "collector-1",
    ) -> None:
        self.devices = devices
        self.metric_source = metric_source
        self.latency_source = latency_source
        self.grpc_client = grpc_client
        self.collection_interval = collection_interval
        self.collector_id = collector_id
        self._previous_samples: dict[str, RawDeviceSample] = {}

    async def poll_once(self) -> None:
        """Poll every device once. Never raises - a single device's
        unhandled error is logged and the round continues.
        """
        for device in self.devices:
            try:
                await self._poll_device(device)
            except Exception:
                logger.exception(
                    "Unhandled error polling device %s - continuing with other devices", device.device_id
                )

    async def _poll_device(self, device: DeviceConfig) -> None:
        sample = await self.metric_source.poll_device(device)
        previous = self._previous_samples.get(device.device_id)
        self._previous_samples[device.device_id] = sample

        interface_metrics = calculate_device_metrics(previous, sample) if previous is not None else []

        latency_result = None
        try:
            latency_result = await self.latency_source.measure(device.device_id, device.ip)
        except Exception:
            logger.exception("Latency measurement failed for device %s", device.device_id)

        batch = build_batch(self.collector_id, device, sample, interface_metrics, latency_result)
        response = await self.grpc_client.send_batch(batch)
        if response is not None and not response.accepted:
            logger.warning("Backend rejected batch for %s: %s", device.device_id, response.message)

    async def run_forever(self) -> None:
        logger.info(
            "Collector starting: %d device(s) configured, poll interval=%.1fs",
            len(self.devices), self.collection_interval,
        )
        while True:
            await self.poll_once()
            await asyncio.sleep(self.collection_interval)

    async def shutdown(self) -> None:
        await self.grpc_client.close()
        await self.metric_source.close()
        await self.latency_source.close()


def build_app_from_config(
    collector_config_path: str = "config/collector.yaml",
    devices_config_path: str = "config/devices.yaml",
) -> CollectorApp:
    config = load_collector_config(collector_config_path)
    devices = load_devices(devices_config_path)
    scenario_controller = ScenarioController()

    metric_source = build_metric_source(config, scenario_controller)
    latency_source = build_latency_source(config, scenario_controller)

    grpc_cfg = config.get("grpc", {})
    grpc_client = TelemetryGrpcClient(
        host=grpc_cfg.get("backend_host", "localhost"),
        port=grpc_cfg.get("backend_port", 50051),
        timeout_seconds=grpc_cfg.get("connect_timeout_seconds", 5.0),
        max_retries=grpc_cfg.get("max_retries", 3),
        retry_backoff_seconds=grpc_cfg.get("retry_backoff_seconds", 2.0),
    )

    return CollectorApp(
        devices=devices,
        metric_source=metric_source,
        latency_source=latency_source,
        grpc_client=grpc_client,
        collection_interval=config.get("collection_interval", 10),
        collector_id=os.environ.get("COLLECTOR_ID", "collector-1"),
    )


async def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    collector_config_path = os.environ.get("COLLECTOR_CONFIG_PATH", "config/collector.yaml")
    devices_config_path = os.environ.get("DEVICES_CONFIG_PATH", "config/devices.yaml")

    app = build_app_from_config(collector_config_path, devices_config_path)
    try:
        await app.run_forever()
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("Collector shutting down")
    finally:
        await app.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
