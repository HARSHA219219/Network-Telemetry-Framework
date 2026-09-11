"""
SNMP credential loading.

Hard requirement from the project: no IP addresses, community strings,
usernames, or passwords are ever hardcoded or stored in YAML. Credentials
are read from environment variables (populated via .env in real
deployments) at the moment a device is polled, keyed by device_id.

Designed so SNMPv3 slots in later without changing SNMPSource's
structure: SNMPCredentials already carries the v3 fields; only
load_snmp_credentials() and the transport's _auth_data() need to grow
an implementation for the V3 branch.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum

from collector.snmp.exceptions import SNMPConfigError


class SNMPVersion(str, Enum):
    V2C = "v2c"
    V3 = "v3"


@dataclass
class SNMPCredentials:
    version: SNMPVersion
    # v2c
    community: str | None = None
    # v3 (modeled now, not yet wired into the transport - see load_snmp_credentials)
    username: str | None = None
    auth_protocol: str | None = None
    auth_password: str | None = None
    priv_protocol: str | None = None
    priv_password: str | None = None


def _env_key_for_device(device_id: str) -> str:
    """'router-01' -> 'ROUTER_01', matching the SNMP_COMMUNITY_<KEY> convention in .env.example."""
    return device_id.upper().replace("-", "_")


def load_snmp_credentials(device_id: str) -> SNMPCredentials:
    """Load this device's SNMP credentials from environment variables.

    Raises SNMPConfigError (not SnmpTimeoutError) when configuration is
    missing - that's a setup problem, distinct from the device being
    unreachable on the network.
    """
    version_str = os.environ.get("SNMP_VERSION", SNMPVersion.V2C.value).lower()
    try:
        version = SNMPVersion(version_str)
    except ValueError as exc:
        raise SNMPConfigError(
            f"Unsupported SNMP_VERSION={version_str!r}. Expected one of: "
            f"{[v.value for v in SNMPVersion]}"
        ) from exc

    key = _env_key_for_device(device_id)

    if version is SNMPVersion.V2C:
        community = os.environ.get(f"SNMP_COMMUNITY_{key}")
        if not community:
            raise SNMPConfigError(
                f"Missing environment variable SNMP_COMMUNITY_{key} for device "
                f"'{device_id}'. Set it in .env before enabling SNMP mode for "
                f"this device (see .env.example)."
            )
        return SNMPCredentials(version=version, community=community)

    # version is SNMPVersion.V3
    raise NotImplementedError(
        "SNMPv3 credential loading is not implemented yet. SNMPCredentials "
        "already models the required fields (username, auth/priv protocol "
        "and passwords) so this can be added in a future phase without "
        "changing SNMPSource or the transport interface."
    )
