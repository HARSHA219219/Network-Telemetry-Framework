"""
SNMP transport abstraction.

SNMPSource never talks to pysnmp directly - it talks to this interface.
That's what makes the collection/discovery logic in snmp_source.py fully
unit-testable with a FakeSnmpTransport (see tests/collector/test_snmp_source.py),
with no live device, no network access, and no dependency on pysnmp's
internals leaking into business logic.

It's also the seam where SNMPv3 gets added later: a new transport
implementation (or a version-aware branch in PysnmpTransport) is the
only thing that needs to change.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from collector.snmp.credentials import SNMPCredentials


class SnmpTransport(ABC):
    """One instance is created per poll, scoped to a single device."""

    def __init__(
        self,
        ip: str,
        port: int,
        credentials: SNMPCredentials,
        timeout_seconds: float,
        retries: int,
    ) -> None:
        self.ip = ip
        self.port = port
        self.credentials = credentials
        self.timeout_seconds = timeout_seconds
        self.retries = retries

    @abstractmethod
    async def get(self, oid: str) -> Any:
        """SNMP GET for a single scalar/indexed OID. Raises SnmpTimeoutError on no response."""
        raise NotImplementedError

    @abstractmethod
    async def walk(self, oid_prefix: str) -> list[tuple[str, Any]]:
        """SNMP GETBULK/GETNEXT walk of a table column.

        Returns a list of (full_oid, value) pairs for every row under
        oid_prefix. Raises SnmpTimeoutError on no response.
        """
        raise NotImplementedError

    @abstractmethod
    async def close(self) -> None:
        """Release any engine/socket resources held by this transport."""
        raise NotImplementedError


class PysnmpTransport(SnmpTransport):
    """Real SNMP transport, backed by pysnmp. Used when collector mode == SNMP.

    Uses pysnmp's synchronous hlapi wrapped in asyncio.to_thread rather
    than pysnmp's asyncio hlapi, since the synchronous API has been
    stable across more pysnmp versions - appropriate for a foundation
    layer that isn't being exercised against a live device yet.
    """

    def __init__(
        self,
        ip: str,
        port: int,
        credentials: SNMPCredentials,
        timeout_seconds: float,
        retries: int,
    ) -> None:
        super().__init__(ip, port, credentials, timeout_seconds, retries)
        self._engine = None  # created lazily so importing this module never requires pysnmp at import time of callers

    def _get_engine(self):
        from pysnmp.hlapi import SnmpEngine

        if self._engine is None:
            self._engine = SnmpEngine()
        return self._engine

    def _auth_data(self):
        from pysnmp.hlapi import CommunityData

        from collector.snmp.credentials import SNMPVersion

        if self.credentials.version is SNMPVersion.V2C:
            # mpModel=1 selects SNMPv2c framing (0 would be SNMPv1)
            return CommunityData(self.credentials.community, mpModel=1)

        raise NotImplementedError("SNMPv3 auth data construction is not implemented yet.")

    async def get(self, oid: str) -> Any:
        import asyncio

        return await asyncio.to_thread(self._get_sync, oid)

    def _get_sync(self, oid: str) -> Any:
        from pysnmp.hlapi import ContextData, ObjectIdentity, ObjectType, UdpTransportTarget, getCmd

        from collector.snmp.exceptions import SnmpError, SnmpTimeoutError

        iterator = getCmd(
            self._get_engine(),
            self._auth_data(),
            UdpTransportTarget((self.ip, self.port), timeout=self.timeout_seconds, retries=self.retries),
            ContextData(),
            ObjectType(ObjectIdentity(oid)),
        )
        error_indication, error_status, _error_index, var_binds = next(iterator)

        if error_indication:
            raise SnmpTimeoutError(f"{self.ip}:{self.port} GET {oid} failed: {error_indication}")
        if error_status:
            raise SnmpError(f"{self.ip}:{self.port} GET {oid} returned error status: {error_status.prettyPrint()}")

        _oid, value = var_binds[0]
        return value

    async def walk(self, oid_prefix: str) -> list[tuple[str, Any]]:
        import asyncio

        return await asyncio.to_thread(self._walk_sync, oid_prefix)

    def _walk_sync(self, oid_prefix: str) -> list[tuple[str, Any]]:
        from pysnmp.hlapi import ContextData, ObjectIdentity, ObjectType, UdpTransportTarget, nextCmd

        from collector.snmp.exceptions import SnmpError, SnmpTimeoutError

        results: list[tuple[str, Any]] = []
        for error_indication, error_status, _error_index, var_binds in nextCmd(
            self._get_engine(),
            self._auth_data(),
            UdpTransportTarget((self.ip, self.port), timeout=self.timeout_seconds, retries=self.retries),
            ContextData(),
            ObjectType(ObjectIdentity(oid_prefix)),
            lexicographicMode=False,  # stop at the end of this table column, don't wander into the next one
        ):
            if error_indication:
                raise SnmpTimeoutError(f"{self.ip}:{self.port} WALK {oid_prefix} failed: {error_indication}")
            if error_status:
                raise SnmpError(
                    f"{self.ip}:{self.port} WALK {oid_prefix} returned error status: {error_status.prettyPrint()}"
                )
            for oid_obj, value in var_binds:
                results.append((str(oid_obj), value))
        return results

    async def close(self) -> None:
        # pysnmp's SnmpEngine has no explicit close needed for the synchronous
        # hlapi transport; kept as a no-op method to satisfy the interface
        # and give future versions (e.g. asyncio hlapi) a place to clean up.
        self._engine = None
