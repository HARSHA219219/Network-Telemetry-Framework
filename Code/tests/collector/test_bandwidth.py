"""
Comprehensive tests for the pure calculation engine. Every test here
uses plain numeric inputs - no SNMP, no mocks, no device involved -
proving the engine is independently testable per requirement 9.
"""

from __future__ import annotations

from collector.metrics.bandwidth import (
    COUNTER32_MAX,
    COUNTER64_MAX,
    calculate_bandwidth,
    calculate_utilization,
)


# --- Normal counter increase ---

def test_normal_counter_increase() -> None:
    result = calculate_bandwidth(previous_counter=1000, current_counter=2000, elapsed_seconds=10)

    assert result.valid is True
    assert result.wrapped is False
    # (2000-1000) bytes * 8 bits/byte / 10s = 800 bps
    assert result.bits_per_second == 800.0


# --- Zero traffic ---

def test_zero_traffic_is_valid_zero_bandwidth() -> None:
    result = calculate_bandwidth(previous_counter=5_000_000, current_counter=5_000_000, elapsed_seconds=10)

    assert result.valid is True
    assert result.bits_per_second == 0.0
    assert result.wrapped is False


# --- High traffic ---

def test_high_traffic_gigabit_scale() -> None:
    # 1.25 GB delta over 10s = 10,000,000,000 bits / 10s = 1 Gbps
    result = calculate_bandwidth(
        previous_counter=0, current_counter=1_250_000_000, elapsed_seconds=10
    )

    assert result.valid is True
    assert result.bits_per_second == 1_000_000_000.0


# --- Different polling intervals ---

def test_same_delta_different_intervals_scales_bandwidth_inversely() -> None:
    delta_bytes = 1_000_000  # 1 MB

    result_5s = calculate_bandwidth(0, delta_bytes, elapsed_seconds=5)
    result_10s = calculate_bandwidth(0, delta_bytes, elapsed_seconds=10)
    result_30s = calculate_bandwidth(0, delta_bytes, elapsed_seconds=30)

    assert result_5s.bits_per_second == (delta_bytes * 8) / 5
    assert result_10s.bits_per_second == (delta_bytes * 8) / 10
    assert result_30s.bits_per_second == (delta_bytes * 8) / 30
    # halving the interval doubles the computed bandwidth for the same delta
    assert result_5s.bits_per_second == result_10s.bits_per_second * 2


def test_elapsed_time_is_explicit_not_assumed() -> None:
    """The engine must use whatever elapsed_seconds it's given, proving it
    never silently substitutes a configured interval - it has no notion
    of one."""
    result_actual = calculate_bandwidth(0, 1000, elapsed_seconds=7.3)
    result_different = calculate_bandwidth(0, 1000, elapsed_seconds=12.9)

    assert result_actual.bits_per_second != result_different.bits_per_second
    assert result_actual.bits_per_second == (1000 * 8) / 7.3


# --- Counter reset ---

def test_counter_reset_is_detected_and_marked_invalid() -> None:
    # Previous ~5GB, current tiny, over only 10s -> implies an impossible
    # multi-terabit/sec wraparound bandwidth -> classified as a reset.
    result = calculate_bandwidth(
        previous_counter=5_000_000_000, current_counter=1000, elapsed_seconds=10
    )

    assert result.valid is False
    assert result.reason == "counter_reset"
    assert result.wrapped is False
    assert result.bits_per_second == 0.0


def test_counter_reset_to_exact_zero() -> None:
    result = calculate_bandwidth(
        previous_counter=999_999_999_999, current_counter=0, elapsed_seconds=10
    )

    assert result.valid is False
    assert result.reason == "counter_reset"


# --- Counter wraparound ---

def test_counter32_wraparound_is_detected_and_valid() -> None:
    # Counter near the 32-bit max, small current value -> a small, plausible
    # wrapped delta, not a reset.
    previous = COUNTER32_MAX - 500
    current = 1000
    result = calculate_bandwidth(previous, current, elapsed_seconds=10, counter_bits=32)

    assert result.valid is True
    assert result.wrapped is True
    # wrapped delta = 500 (remaining to max) + 1000 (past zero) + 1 = 1501 bytes
    expected_bps = (1501 * 8) / 10
    assert result.bits_per_second == expected_bps


def test_counter64_wraparound_is_detected_and_valid() -> None:
    previous = COUNTER64_MAX - 200
    current = 300
    result = calculate_bandwidth(previous, current, elapsed_seconds=10, counter_bits=64)

    assert result.valid is True
    assert result.wrapped is True
    expected_delta = 200 + 300 + 1
    assert result.bits_per_second == (expected_delta * 8) / 10


def test_wraparound_vs_reset_boundary_respects_plausibility_ceiling() -> None:
    # Construct a wrapped delta whose implied bandwidth sits just outside
    # a tight custom ceiling -> must be classified as a reset, not wraparound.
    previous = COUNTER32_MAX - 10
    current = 10
    # wrapped delta = 10 + 10 + 1 = 21 bytes = 168 bits over 1s = 168 bps
    result = calculate_bandwidth(
        previous, current, elapsed_seconds=1, counter_bits=32, max_plausible_bps=100
    )

    assert result.valid is False
    assert result.reason == "counter_reset"


# --- Invalid / negative inputs ---

def test_negative_previous_counter_is_invalid() -> None:
    result = calculate_bandwidth(previous_counter=-1, current_counter=100, elapsed_seconds=10)

    assert result.valid is False
    assert result.reason == "negative_counter"


def test_negative_current_counter_is_invalid() -> None:
    result = calculate_bandwidth(previous_counter=100, current_counter=-1, elapsed_seconds=10)

    assert result.valid is False
    assert result.reason == "negative_counter"


def test_zero_elapsed_time_is_invalid() -> None:
    result = calculate_bandwidth(previous_counter=100, current_counter=200, elapsed_seconds=0)

    assert result.valid is False
    assert result.reason == "non_positive_elapsed_time"


def test_negative_elapsed_time_is_invalid() -> None:
    result = calculate_bandwidth(previous_counter=100, current_counter=200, elapsed_seconds=-5)

    assert result.valid is False
    assert result.reason == "non_positive_elapsed_time"


def test_invalid_counter_bits_is_rejected() -> None:
    result = calculate_bandwidth(previous_counter=100, current_counter=200, elapsed_seconds=10, counter_bits=16)

    assert result.valid is False
    assert result.reason == "invalid_counter_bits"


def test_counter_exceeding_declared_bit_width_is_rejected() -> None:
    # A "32-bit" counter value that's actually larger than COUNTER32_MAX
    # indicates a mismatch between declared width and actual data.
    result = calculate_bandwidth(
        previous_counter=COUNTER32_MAX + 100, current_counter=COUNTER32_MAX + 200,
        elapsed_seconds=10, counter_bits=32,
    )

    assert result.valid is False
    assert result.reason == "counter_exceeds_bit_width"


# --- Utilization calculation ---

def test_utilization_basic_percentage() -> None:
    result = calculate_utilization(bandwidth_bps=500_000_000, interface_speed_bps=1_000_000_000)

    assert result.valid is True
    assert result.percent == 50.0


def test_utilization_full_saturation() -> None:
    result = calculate_utilization(bandwidth_bps=1_000_000_000, interface_speed_bps=1_000_000_000)

    assert result.valid is True
    assert result.percent == 100.0


def test_utilization_zero_bandwidth() -> None:
    result = calculate_utilization(bandwidth_bps=0, interface_speed_bps=1_000_000_000)

    assert result.valid is True
    assert result.percent == 0.0


def test_utilization_can_exceed_100_without_being_clamped() -> None:
    # Deliberately not clamped - measurement noise or a speed
    # misreport should be visible, not silently hidden.
    result = calculate_utilization(bandwidth_bps=1_200_000_000, interface_speed_bps=1_000_000_000)

    assert result.valid is True
    assert result.percent == 120.0


# --- Interface speed edge cases ---

def test_utilization_zero_interface_speed_is_undefined() -> None:
    result = calculate_utilization(bandwidth_bps=500_000_000, interface_speed_bps=0)

    assert result.valid is False
    assert result.percent is None
    assert result.reason == "invalid_interface_speed"


def test_utilization_negative_interface_speed_is_invalid() -> None:
    result = calculate_utilization(bandwidth_bps=500_000_000, interface_speed_bps=-1000)

    assert result.valid is False
    assert result.percent is None
    assert result.reason == "invalid_interface_speed"


def test_utilization_negative_bandwidth_is_invalid() -> None:
    result = calculate_utilization(bandwidth_bps=-100, interface_speed_bps=1_000_000_000)

    assert result.valid is False
    assert result.reason == "negative_bandwidth"
