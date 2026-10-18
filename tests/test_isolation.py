"""Each test starts from the provisioned state, whatever the one before it did. Run in file order."""

from __future__ import annotations

import pytest

from tsqltap.connection import Db
from tsqltap.provision import Environment


def test_transaction_changes_are_rolled_back_1(mssql_db: Db) -> None:
    mssql_db.execute("INSERT dbo.Region (RegionCode, Name) VALUES ('T1', N'Temp')")
    assert mssql_db.scalar("SELECT COUNT(*) FROM dbo.Region") == 3


def test_transaction_changes_are_rolled_back_2(mssql_db: Db) -> None:
    assert mssql_db.scalar("SELECT COUNT(*) FROM dbo.Region") == 2


@pytest.mark.mssql(isolation="snapshot")
def test_snapshot_lets_code_commit_1(mssql_db: Db) -> None:
    mssql_db.execute("EXEC dbo.usp_AddRegionOrRollBack @RegionCode = 'C1', @Name = N'Committed'")
    assert mssql_db.trancount == 0
    assert mssql_db.scalar("SELECT COUNT(*) FROM dbo.Region WHERE RegionCode = 'C1'") == 1


def test_snapshot_lets_code_commit_2(mssql_db: Db) -> None:
    assert mssql_db.scalar("SELECT COUNT(*) FROM dbo.Region WHERE RegionCode IN ('C1', 'W1')") == 0


def test_restore_keeps_the_tap_library_and_seed(mssql_env: Environment) -> None:
    mssql_env.restore("TsqltapSelf")
    with mssql_env.connect("TsqltapSelf") as db:
        assert db.scalar("SELECT OBJECT_ID(N'tap.results_eq')") is not None
        assert db.scalar("SELECT COUNT(*) FROM dbo.Region") == 2


@pytest.mark.mssql(database="TsqltapOther")
def test_marker_picks_the_database(mssql_db: Db) -> None:
    assert mssql_db.scalar("SELECT DB_NAME()") == "TsqltapOther"


@pytest.mark.mssql(requires=["azure"])
def test_requires_skips(mssql_db: Db) -> None:
    raise AssertionError("should have been skipped")


def test_fake_and_spy_from_python(mssql_db: Db) -> None:
    mssql_db.fake_table("dbo.Trust")
    mssql_db.seed("dbo.Trust", [{"RegionCode": "anything"}])  # FK gone with the fake
    mssql_db.spy_procedure("dbo.usp_Notify")
    mssql_db.execute("EXEC dbo.usp_Notify @Message = N'hi', @Level = 3")
    assert [(c["Message"], c["Level"]) for c in mssql_db.spy_calls("dbo.usp_Notify")] == [("hi", 3)]


@pytest.mark.mssql(isolation="snapshot", restore=["TsqltapOther"])
def test_restore_other_databases_1(mssql_db: Db, mssql_env: Environment) -> None:
    with mssql_env.connect("TsqltapOther") as other:
        other.execute("CREATE TABLE dbo.Committed (n INT)")


def test_restore_other_databases_2(mssql_env: Environment) -> None:
    with mssql_env.connect("TsqltapOther") as other:
        assert other.scalar("SELECT OBJECT_ID(N'dbo.Committed')") is None
