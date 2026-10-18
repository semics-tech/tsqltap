"""Connections are pooled between tests (opening one costs ~150 ms through Docker's port forwarding),
but only when a test leaves its session exactly as it found it. Run in file order."""

from __future__ import annotations

import pytest

from tsqltap.connection import Db
from tsqltap.provision import DIRTY_PROPERTY, Environment

_seen: dict[str, str] = {}
CONNECTION_ID = "SELECT CONVERT(NVARCHAR(36), connection_id) FROM sys.dm_exec_connections WHERE session_id = @@SPID"
# (not @@SPID: SQL Server hands a closed session's number to the next connection straight away)


def test_a_clean_test_returns_its_connection_1(mssql_db: Db) -> None:
    _seen["connection"] = str(mssql_db.scalar(CONNECTION_ID))


def test_a_clean_test_returns_its_connection_2(mssql_db: Db) -> None:
    assert str(mssql_db.scalar(CONNECTION_ID)) == _seen["connection"], "the next test gets the same connection"


def test_changed_session_options_do_not_leak_1(mssql_db: Db) -> None:
    mssql_db.execute("SET DATEFIRST 1")
    _seen["connection"] = str(mssql_db.scalar(CONNECTION_ID))


def test_changed_session_options_do_not_leak_2(mssql_db: Db) -> None:
    assert int(mssql_db.scalar("SELECT @@DATEFIRST")) == 7
    assert str(mssql_db.scalar(CONNECTION_ID)) != _seen["connection"], "the changed connection was not pooled"


def _dirty(env: Environment, name: str) -> bool:
    return bool(
        env.master().scalar(f"SELECT COUNT(*) FROM [{name}].sys.extended_properties WHERE name = ?", DIRTY_PROPERTY)
    )


@pytest.mark.mssql(isolation="none", database="TsqltapOther")
def test_a_committing_test_marks_its_database_dirty_1(mssql_db: Db, mssql_env: Environment) -> None:
    assert _dirty(mssql_env, "TsqltapOther"), "marked before the test runs"


def test_a_committing_test_marks_its_database_dirty_2(mssql_env: Environment) -> None:
    assert _dirty(mssql_env, "TsqltapOther"), "isolation=none is not restored, so the marker stays for the next session"
    mssql_env.restore("TsqltapOther")
    assert not _dirty(mssql_env, "TsqltapOther"), "a restore removes it (the snapshot predates it)"


def test_query_store_is_off_so_restores_do_not_wait_for_its_flush(mssql_db: Db) -> None:
    # SQL Server 2022+ turns Query Store on for every new database; its background flush task holds
    # SINGLE_USER WITH ROLLBACK IMMEDIATE for ~3 s, on every snapshot restore.
    assert mssql_db.scalar("SELECT actual_state_desc FROM sys.database_query_store_options") == "OFF"
