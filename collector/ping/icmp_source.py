"""
Real ICMP latency measurement.

Uses the system `ping` utility via subprocess rather than raw sockets:
raw ICMP sockets require elevated/administrator privileges on both
Windows and Linux, which is an unreasonable requirement for a student
project's collector process. Shelling out to `ping` (present on every
Windows and Linux install by default) avoids that entirely and is
exactly how most lightweight monitoring tools do this.

Windows and Linux `ping` have different flags and output formats, so
this module detects the platform and parses accordingly. Developed and
tested primarily against Windows (PowerShell) per project requirements,
with a Linux code path for the Docker deployment target.
"""

from __future__ import annotations

import asyncio
import logging
import platform
import re

from collector.ping.base import IcmpResult, LatencySource

logger = logging.getLogger(__name__)


class IcmpPingSource(LatencySource):
    """Real latency measurement via the OS `ping` command."""

    def __init__(self, probes_per_measurement: int = 4, timeout_seconds: float = 1.0) -> None:
        self._count = probes_per_measurement
        self._timeout_seconds = timeout_seconds
        self._is_windows = platform.system().lower() == "windows"

    def _build_command(self, device_ip: str) -> list[str]:
        if self._is_windows:
            # -n count, -w timeout-per-reply-in-ms
            timeout_ms = int(self._timeout_seconds * 1000)
            return ["ping", "-n", str(self._count), "-w", str(timeout_ms), device_ip]
        # Linux: -c count, -W timeout-per-reply-in-seconds (integer)
        timeout_s = max(1, int(self._timeout_seconds))
        return ["ping", "-c", str(self._count), "-W", str(timeout_s), device_ip]

    async def measure(self, device_id: str, device_ip: str) -> IcmpResult:
        command = self._build_command(device_ip)

        try:
            proc = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            overall_timeout = self._timeout_seconds * self._count + 5
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(), timeout=overall_timeout
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                logger.warning("ping to %s (%s) exceeded overall timeout", device_id, device_ip)
                return IcmpResult(
                    device_id=device_id, device_ip=device_ip, reachable=False,
                    packet_loss_percent=100.0, samples_sent=self._count, samples_received=0,
                )
        except FileNotFoundError:
            # `ping` not on PATH - a setup problem, not a device-down condition,
            # but we still must not crash the collector for other devices.
            logger.error("ping executable not found on PATH; cannot measure latency for %s", device_id)
            return IcmpResult(
                device_id=device_id, device_ip=device_ip, reachable=False,
                packet_loss_percent=100.0, samples_sent=0, samples_received=0,
            )
        except PermissionError as exc:
            logger.error("permission denied running ping for %s: %s", device_id, exc)
            return IcmpResult(
                device_id=device_id, device_ip=device_ip, reachable=False,
                packet_loss_percent=100.0, samples_sent=0, samples_received=0,
            )

        output = stdout_bytes.decode(errors="replace") + stderr_bytes.decode(errors="replace")
        return self._parse_output(device_id, device_ip, output)

    def _parse_output(self, device_id: str, device_ip: str, output: str) -> IcmpResult:
        return (
            self._parse_windows_output(device_id, device_ip, output)
            if self._is_windows
            else self._parse_linux_output(device_id, device_ip, output)
        )

    def _parse_windows_output(self, device_id: str, device_ip: str, output: str) -> IcmpResult:
        # "Packets: Sent = 4, Received = 4, Lost = 0 (0% loss)"
        packet_match = re.search(
            r"Sent\s*=\s*(\d+),\s*Received\s*=\s*(\d+),\s*Lost\s*=\s*(\d+)\s*\((\d+)%\s*loss\)",
            output,
        )
        # "Minimum = 1ms, Maximum = 3ms, Average = 2ms"
        rtt_match = re.search(
            r"Minimum\s*=\s*(\d+)ms,\s*Maximum\s*=\s*(\d+)ms,\s*Average\s*=\s*(\d+)ms",
            output,
        )

        if not packet_match:
            # Malformed/unexpected output - treat conservatively as unreachable
            # rather than raising, since a garbled reply is still "no usable answer".
            logger.warning("Could not parse ping output for %s (%s); output: %r", device_id, device_ip, output[:200])
            return IcmpResult(
                device_id=device_id, device_ip=device_ip, reachable=False,
                packet_loss_percent=100.0, samples_sent=self._count, samples_received=0,
            )

        sent, received, _lost, loss_pct = (int(g) for g in packet_match.groups())

        if received == 0 or not rtt_match:
            return IcmpResult(
                device_id=device_id, device_ip=device_ip, reachable=False,
                packet_loss_percent=float(loss_pct), samples_sent=sent, samples_received=received,
            )

        min_ms, max_ms, avg_ms = (float(g) for g in rtt_match.groups())
        return IcmpResult(
            device_id=device_id, device_ip=device_ip, reachable=True,
            avg_ms=avg_ms, min_ms=min_ms, max_ms=max_ms,
            packet_loss_percent=float(loss_pct), samples_sent=sent, samples_received=received,
        )

    def _parse_linux_output(self, device_id: str, device_ip: str, output: str) -> IcmpResult:
        # "4 packets transmitted, 4 received, 0% packet loss, time 3003ms"
        packet_match = re.search(
            r"(\d+)\s+packets transmitted,\s*(\d+)\s+received,.*?(\d+(?:\.\d+)?)%\s*packet loss",
            output,
        )
        # "rtt min/avg/max/mdev = 0.020/0.025/0.030/0.005 ms"
        rtt_match = re.search(
            r"[= ](\d+(?:\.\d+)?)/(\d+(?:\.\d+)?)/(\d+(?:\.\d+)?)/\d+(?:\.\d+)?\s*ms",
            output,
        )

        if not packet_match:
            logger.warning("Could not parse ping output for %s (%s); output: %r", device_id, device_ip, output[:200])
            return IcmpResult(
                device_id=device_id, device_ip=device_ip, reachable=False,
                packet_loss_percent=100.0, samples_sent=self._count, samples_received=0,
            )

        sent, received, loss_pct = int(packet_match.group(1)), int(packet_match.group(2)), float(packet_match.group(3))

        if received == 0 or not rtt_match:
            return IcmpResult(
                device_id=device_id, device_ip=device_ip, reachable=False,
                packet_loss_percent=loss_pct, samples_sent=sent, samples_received=received,
            )

        min_ms, avg_ms, max_ms = (float(g) for g in rtt_match.groups())
        return IcmpResult(
            device_id=device_id, device_ip=device_ip, reachable=True,
            avg_ms=avg_ms, min_ms=min_ms, max_ms=max_ms,
            packet_loss_percent=loss_pct, samples_sent=sent, samples_received=received,
        )

    async def close(self) -> None:
        return None
