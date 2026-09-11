"""Exceptions raised by the SNMP collection layer.

Kept separate from generic Python exceptions so callers (SNMPSource,
tests) can distinguish "device didn't answer" (expected, handled by
marking the device unreachable) from "we're misconfigured" (should
surface loudly, not be silently swallowed as a device outage).
"""


class SnmpError(Exception):
    """Base class for all SNMP-layer errors."""


class SnmpTimeoutError(SnmpError):
    """The device did not respond within the configured timeout/retries."""


class SNMPConfigError(SnmpError):
    """Required SNMP configuration (credentials, version, etc.) is missing or invalid.

    This is a configuration/programmer problem, not a network condition -
    it should not be treated the same as a device being down.
    """
