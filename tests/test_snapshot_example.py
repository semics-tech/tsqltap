"""Result-set snapshot testing with syrupy: `pytest --snapshot-update` records, later runs compare."""

from __future__ import annotations

from typing import Any

from tsqltap.connection import Db


def test_active_trusts_snapshot(mssql_db: Db, snapshot: Any) -> None:
    mssql_db.seed("dbo.Trust", [{"TrustCode": "A1", "RegionCode": "N1", "Name": "Alpha"}])
    assert mssql_db.rows("SELECT TrustCode, Name, RegionName FROM dbo.vw_ActiveTrust ORDER BY TrustCode") == snapshot
