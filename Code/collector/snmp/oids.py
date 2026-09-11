"""
OID constants for SNMP polling.

Sources:
  - MIB-2 System group      (RFC 1213)
  - IF-MIB ifTable/ifXTable (RFC 2863)

ifTable holds the original, 32-bit-counter interface columns.
ifXTable is the extension table added later specifically to provide
64-bit ("HC" = High Capacity) counters and Mbps-scale speed, because
32-bit byte counters wrap in seconds on fast links. We deliberately mix
columns from both tables - see README Phase 2 section for the full
rationale on each choice.
"""

from __future__ import annotations

# --- System group (device-level, un-indexed scalars; note trailing .0) ---
OID_SYS_UPTIME = "1.3.6.1.2.1.1.3.0"     # TimeTicks, hundredths of a second since boot
OID_SYS_NAME = "1.3.6.1.2.1.1.5.0"        # device hostname

# --- ifXTable (indexed by ifIndex; append ".{if_index}") ---
OID_IF_NAME = "1.3.6.1.2.1.31.1.1.1.1"          # ifName        - short stable interface name
OID_IF_HIGH_SPEED = "1.3.6.1.2.1.31.1.1.1.15"     # ifHighSpeed   - capacity in Mbps (32-bit safe)
OID_IF_HC_IN_OCTETS = "1.3.6.1.2.1.31.1.1.1.6"     # ifHCInOctets  - 64-bit cumulative bytes in
OID_IF_HC_OUT_OCTETS = "1.3.6.1.2.1.31.1.1.1.10"    # ifHCOutOctets - 64-bit cumulative bytes out

# --- ifTable (indexed by ifIndex; append ".{if_index}") ---
OID_IF_OPER_STATUS = "1.3.6.1.2.1.2.2.1.8"    # ifOperStatus     - 1=up, 2=down, 3=testing, ...
OID_IF_IN_UCAST_PKTS = "1.3.6.1.2.1.2.2.1.11"   # ifInUcastPkts    - cumulative packets in
OID_IF_OUT_UCAST_PKTS = "1.3.6.1.2.1.2.2.1.17"   # ifOutUcastPkts   - cumulative packets out
OID_IF_IN_ERRORS = "1.3.6.1.2.1.2.2.1.14"       # ifInErrors       - cumulative input errors
OID_IF_OUT_ERRORS = "1.3.6.1.2.1.2.2.1.20"       # ifOutErrors      - cumulative output errors

IF_OPER_STATUS_UP = 1


def indexed_oid(base_oid: str, if_index: int) -> str:
    """Append an ifIndex to a base table OID, e.g. ('1.3.6...1.6', 3) -> '1.3.6...1.6.3'."""
    return f"{base_oid}.{if_index}"


def extract_index_suffix(full_oid: str, base_oid: str) -> int:
    """Given a full OID returned by a table walk and its known base, return the trailing ifIndex.

    e.g. extract_index_suffix('1.3.6.1.2.1.31.1.1.1.1.3', OID_IF_NAME) -> 3
    """
    if not full_oid.startswith(base_oid + "."):
        raise ValueError(f"OID {full_oid!r} does not start with expected base {base_oid!r}")
    return int(full_oid[len(base_oid) + 1 :])
