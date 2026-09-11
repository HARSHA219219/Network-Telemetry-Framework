"""
Pure bandwidth/utilization calculation engine.

Deliberately independent of SNMP, gRPC, and the telemetry dataclasses -
every function here takes and returns plain numbers/dataclasses of
numbers, so the math can be unit-tested without any device, mock
transport, or knowledge of where the counters came from.

Formulas:
    bandwidth_bps   = (delta_bytes * 8) / elapsed_seconds
    utilization_pct = (bandwidth_bps / interface_speed_bps) * 100

Counter handling:
    A counter is expected to be monotonically non-decreasing between
    polls. When current < previous, that's either:
      - wraparound: the counter hit its max value and rolled over while
        genuinely still counting traffic, or
      - a reset: the device rebooted / counter was cleared, and current
        has no valid mathematical relationship to previous.
    We disambiguate with a plausibility check: compute the wraparound-
    corrected delta, see what bandwidth it implies, and compare against
    a generous ceiling. Implausibly high implied bandwidth -> reset,
    not wraparound. See calculate_bandwidth() docstring for details.
"""

from __future__ import annotations

from dataclasses import dataclass

COUNTER32_MAX = 2**32 - 1          # 4,294,967,295
COUNTER64_MAX = 2**64 - 1          # ~1.8e19

# Ceiling used to decide "wraparound" vs "reset" when current < previous.
# Generous on purpose - well above any real interface speed we'd expect
# to see in this project - so we only classify something as a reset when
# the wraparound-corrected delta is truly implausible.
DEFAULT_MAX_PLAUSIBLE_BPS = 200_000_000_000  # 200 Gbps

_VALID_COUNTER_BITS = (32, 64)


@dataclass
class BandwidthResult:
    bits_per_second: float
    valid: bool
    wrapped: bool = False
    reason: str | None = None   # populated when valid is False, or notes wraparound


@dataclass
class UtilizationResult:
    percent: float | None
    valid: bool
    reason: str | None = None


def _counter_max(counter_bits: int) -> int:
    return COUNTER64_MAX if counter_bits == 64 else COUNTER32_MAX


def calculate_bandwidth(
    previous_counter: int,
    current_counter: int,
    elapsed_seconds: float,
    counter_bits: int = 64,
    max_plausible_bps: float = DEFAULT_MAX_PLAUSIBLE_BPS,
) -> BandwidthResult:
    """Calculate bandwidth (bits/sec) from two counter samples.

    counter_bits: width of the source counter (64 for ifHCIn/OutOctets,
    the default and the preferred SNMP layer choice; 32 supported for
    completeness / legacy fallback).
    """
    if counter_bits not in _VALID_COUNTER_BITS:
        return BandwidthResult(bits_per_second=0.0, valid=False, reason="invalid_counter_bits")

    if elapsed_seconds <= 0:
        return BandwidthResult(bits_per_second=0.0, valid=False, reason="non_positive_elapsed_time")

    if previous_counter < 0 or current_counter < 0:
        return BandwidthResult(bits_per_second=0.0, valid=False, reason="negative_counter")

    counter_max = _counter_max(counter_bits)
    if previous_counter > counter_max or current_counter > counter_max:
        return BandwidthResult(bits_per_second=0.0, valid=False, reason="counter_exceeds_bit_width")

    if current_counter >= previous_counter:
        delta_bytes = current_counter - previous_counter
        wrapped = False
    else:
        # current < previous: wraparound or reset - disambiguate by plausibility.
        wrapped_delta = (counter_max - previous_counter) + current_counter + 1
        implied_bps = (wrapped_delta * 8) / elapsed_seconds

        if implied_bps <= max_plausible_bps:
            delta_bytes = wrapped_delta
            wrapped = True
        else:
            # Implausibly large - almost certainly a reboot/counter reset,
            # not a genuine wraparound. No valid delta for this interval.
            return BandwidthResult(bits_per_second=0.0, valid=False, reason="counter_reset")

    bits_per_second = (delta_bytes * 8) / elapsed_seconds
    return BandwidthResult(bits_per_second=bits_per_second, valid=True, wrapped=wrapped)


def calculate_utilization(bandwidth_bps: float, interface_speed_bps: int) -> UtilizationResult:
    """Calculate utilization percentage given a bandwidth and interface capacity."""
    if interface_speed_bps <= 0:
        # Common for administratively-down interfaces reporting speed 0 -
        # utilization is undefined, not zero.
        return UtilizationResult(percent=None, valid=False, reason="invalid_interface_speed")

    if bandwidth_bps < 0:
        return UtilizationResult(percent=None, valid=False, reason="negative_bandwidth")

    percent = (bandwidth_bps / interface_speed_bps) * 100
    return UtilizationResult(percent=percent, valid=True)
