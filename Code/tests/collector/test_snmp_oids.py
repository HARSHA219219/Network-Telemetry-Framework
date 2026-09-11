from __future__ import annotations

import pytest

from collector.snmp.oids import OID_IF_NAME, extract_index_suffix, indexed_oid


def test_indexed_oid_appends_index() -> None:
    assert indexed_oid("1.3.6.1.2.1.31.1.1.1.6", 3) == "1.3.6.1.2.1.31.1.1.1.6.3"


def test_extract_index_suffix_returns_trailing_int() -> None:
    full_oid = "1.3.6.1.2.1.31.1.1.1.1.7"
    assert extract_index_suffix(full_oid, OID_IF_NAME) == 7


def test_extract_index_suffix_rejects_mismatched_base() -> None:
    with pytest.raises(ValueError):
        extract_index_suffix("1.2.3.4.5", OID_IF_NAME)
