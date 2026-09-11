"""
Tests for IcmpPingSource. Real ICMP/subprocess access is mocked - these
tests exercise the OUTPUT PARSING logic (the part that's actually risky
to get wrong) using canned ping output captured from both platforms.
"""

from __future__ import annotations

import asyncio

from collector.ping.icmp_source import IcmpPingSource

WINDOWS_SUCCESS_OUTPUT = """
Pinging 10.0.0.1 with 32 bytes of data:
Reply from 10.0.0.1: bytes=32 time=1ms TTL=64
Reply from 10.0.0.1: bytes=32 time=2ms TTL=64
Reply from 10.0.0.1: bytes=32 time=1ms TTL=64
Reply from 10.0.0.1: bytes=32 time=3ms TTL=64

Ping statistics for 10.0.0.1:
    Packets: Sent = 4, Received = 4, Lost = 0 (0% loss),
Approximate round trip times in milli-seconds:
    Minimum = 1ms, Maximum = 3ms, Average = 2ms
"""

WINDOWS_PARTIAL_LOSS_OUTPUT = """
Pinging 10.0.0.2 with 32 bytes of data:
Reply from 10.0.0.2: bytes=32 time=5ms TTL=64
Request timed out.
Reply from 10.0.0.2: bytes=32 time=6ms TTL=64
Reply from 10.0.0.2: bytes=32 time=4ms TTL=64

Ping statistics for 10.0.0.2:
    Packets: Sent = 4, Received = 3, Lost = 1 (25% loss),
Approximate round trip times in milli-seconds:
    Minimum = 4ms, Maximum = 6ms, Average = 5ms
"""

WINDOWS_UNREACHABLE_OUTPUT = """
Pinging 10.0.0.99 with 32 bytes of data:
Request timed out.
Request timed out.
Request timed out.
Request timed out.

Ping statistics for 10.0.0.99:
    Packets: Sent = 4, Received = 0, Lost = 4 (100% loss),
"""

LINUX_SUCCESS_OUTPUT = """
PING 10.0.0.1 (10.0.0.1) 56(84) bytes of data.
64 bytes from 10.0.0.1: icmp_seq=1 ttl=64 time=0.020 ms
64 bytes from 10.0.0.1: icmp_seq=2 ttl=64 time=0.025 ms
64 bytes from 10.0.0.1: icmp_seq=3 ttl=64 time=0.030 ms
64 bytes from 10.0.0.1: icmp_seq=4 ttl=64 time=0.022 ms

--- 10.0.0.1 ping statistics ---
4 packets transmitted, 4 received, 0% packet loss, time 3003ms
rtt min/avg/max/mdev = 0.020/0.025/0.030/0.005 ms
"""

LINUX_UNREACHABLE_OUTPUT = """
PING 10.0.0.99 (10.0.0.99) 56(84) bytes of data.

--- 10.0.0.99 ping statistics ---
4 packets transmitted, 0 received, 100% packet loss, time 3060ms
"""


def _windows_source() -> IcmpPingSource:
    source = IcmpPingSource(probes_per_measurement=4, timeout_seconds=1.0)
    source._is_windows = True
    return source


def _linux_source() -> IcmpPingSource:
    source = IcmpPingSource(probes_per_measurement=4, timeout_seconds=1.0)
    source._is_windows = False
    return source


# --- Successful replies ---

def test_windows_successful_reply_parsing() -> None:
    source = _windows_source()
    result = source._parse_output("router-01", "10.0.0.1", WINDOWS_SUCCESS_OUTPUT)

    assert result.reachable is True
    assert result.avg_ms == 2.0
    assert result.min_ms == 1.0
    assert result.max_ms == 3.0
    assert result.packet_loss_percent == 0.0
    assert result.samples_sent == 4
    assert result.samples_received == 4


def test_linux_successful_reply_parsing() -> None:
    source = _linux_source()
    result = source._parse_output("router-01", "10.0.0.1", LINUX_SUCCESS_OUTPUT)

    assert result.reachable is True
    assert result.avg_ms == 0.025
    assert result.min_ms == 0.020
    assert result.max_ms == 0.030
    assert result.packet_loss_percent == 0.0


# --- Partial packet loss ---

def test_windows_partial_packet_loss() -> None:
    source = _windows_source()
    result = source._parse_output("router-02", "10.0.0.2", WINDOWS_PARTIAL_LOSS_OUTPUT)

    assert result.reachable is True  # some replies came back
    assert result.packet_loss_percent == 25.0
    assert result.samples_sent == 4
    assert result.samples_received == 3
    assert result.avg_ms == 5.0


# --- Unreachable device (100% loss) ---

def test_windows_unreachable_device() -> None:
    source = _windows_source()
    result = source._parse_output("router-99", "10.0.0.99", WINDOWS_UNREACHABLE_OUTPUT)

    assert result.reachable is False
    assert result.packet_loss_percent == 100.0
    assert result.samples_received == 0


def test_linux_unreachable_device() -> None:
    source = _linux_source()
    result = source._parse_output("router-99", "10.0.0.99", LINUX_UNREACHABLE_OUTPUT)

    assert result.reachable is False
    assert result.packet_loss_percent == 100.0
    assert result.samples_received == 0


# --- Malformed output ---

def test_malformed_output_is_treated_as_unreachable_not_a_crash() -> None:
    source = _windows_source()
    result = source._parse_output("router-01", "10.0.0.1", "garbage output that matches nothing")

    assert result.reachable is False
    assert result.packet_loss_percent == 100.0


# --- FileNotFoundError / PermissionError / timeout via measure() ---

def test_measure_handles_missing_ping_executable(monkeypatch) -> None:
    async def fake_create_subprocess_exec(*args, **kwargs):
        raise FileNotFoundError("ping not found")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    source = _windows_source()
    result = asyncio.run(source.measure("router-01", "10.0.0.1"))

    assert result.reachable is False
    assert result.samples_sent == 0


def test_measure_handles_permission_error(monkeypatch) -> None:
    async def fake_create_subprocess_exec(*args, **kwargs):
        raise PermissionError("permission denied")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    source = _windows_source()
    result = asyncio.run(source.measure("router-01", "10.0.0.1"))

    assert result.reachable is False


def test_measure_handles_overall_timeout(monkeypatch) -> None:
    class _FakeProc:
        async def communicate(self):
            await asyncio.sleep(100)  # never completes within the test timeout

        def kill(self):
            pass

        async def wait(self):
            return None

    async def fake_create_subprocess_exec(*args, **kwargs):
        return _FakeProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    source = IcmpPingSource(probes_per_measurement=1, timeout_seconds=0.01)
    result = asyncio.run(source.measure("router-01", "10.0.0.1"))

    assert result.reachable is False
    assert result.packet_loss_percent == 100.0
