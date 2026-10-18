from __future__ import annotations

from pathlib import Path

import pytest

from tsqltap.connection import Db
from tsqltap.seed import SeedError, load_files, read_csv


def test_autofills_required_columns(mssql_db: Db) -> None:
    # Trust needs TrustCode, RegionCode, Name and OpenedOn; give only what the test cares about.
    mssql_db.seed("dbo.Trust", [{"RegionCode": "N1"}, {"RegionCode": "S1"}])
    rows = mssql_db.rows("SELECT TrustCode, Name, OpenedOn, IsActive FROM dbo.Trust ORDER BY TrustId")
    assert len(rows) == 2
    assert rows[0]["TrustCode"] != rows[1]["TrustCode"]  # distinct, so the UNIQUE constraint holds
    assert all(r["IsActive"] for r in rows)  # defaults are left to the table, not filled in


def test_defaults_apply_to_every_row(mssql_db: Db) -> None:
    mssql_db.seed("dbo.Trust", [{"TrustCode": "A"}, {"TrustCode": "B"}], defaults={"RegionCode": "N1", "Score": "12.5"})
    assert mssql_db.rows("SELECT DISTINCT RegionCode, Score FROM dbo.Trust") == [
        {"RegionCode": "N1", "Score": pytest.approx(12.5)}
    ]


def test_constraints_still_apply(mssql_db: Db) -> None:
    from tsqltap.connection import BatchError

    with pytest.raises(BatchError, match="FK_Trust_Region"):
        mssql_db.seed("dbo.Trust", [{"RegionCode": "NOPE"}])


def test_unknown_column(mssql_db: Db) -> None:
    with pytest.raises(SeedError, match="no column"):
        mssql_db.seed("dbo.Region", [{"RegionCode": "X", "Colour": "blue"}])


def test_identity_values_are_kept(mssql_db: Db) -> None:
    mssql_db.seed("dbo.Trust", [{"TrustId": 500, "RegionCode": "N1"}])
    assert mssql_db.scalar("SELECT TrustId FROM dbo.Trust") == 500


def test_csv_nulls_and_table_header(tmp_path: Path, mssql_db: Db) -> None:
    f = tmp_path / "trusts.csv"
    f.write_text(
        "-- table: dbo.Trust\nTrustCode,RegionCode,Name,Score,OpenedOn\n"
        "C1,N1,Csv One,,2026-02-01\nC2,N1,Csv Two,\\N,2026-02-02\nC3,S1,Csv Three,99.5,2026-02-03\n",
        encoding="utf-8",
    )
    table, rows = read_csv(f)
    assert table == "dbo.Trust"
    assert rows[0]["Score"] is None and rows[1]["Score"] is None
    assert load_files(mssql_db, [f]) == 3
    assert mssql_db.rows("SELECT TrustCode, Score FROM dbo.Trust WHERE Score IS NOT NULL") == [
        {"TrustCode": "C3", "Score": pytest.approx(99.5)}
    ]


def test_csv_table_from_file_name(tmp_path: Path) -> None:
    (tmp_path / "App.Notification.csv").write_text("Title\nx\n", encoding="utf-8")
    (tmp_path / "Region.csv").write_text("RegionCode\nx\n", encoding="utf-8")
    assert read_csv(tmp_path / "App.Notification.csv")[0] == "App.Notification"
    assert read_csv(tmp_path / "Region.csv")[0] == "dbo.Region"


def test_files_load_parents_first(tmp_path: Path, mssql_db: Db) -> None:
    (tmp_path / "a_child.csv").write_text("-- table: dbo.Trust\nTrustCode,RegionCode\nK1,K\n", encoding="utf-8")
    (tmp_path / "b_parent.json").write_text('{"dbo.Region": [{"RegionCode": "K", "Name": "Kent"}]}', encoding="utf-8")
    assert load_files(mssql_db, sorted(tmp_path.iterdir())) == 2


def test_baseline_seed_was_loaded(mssql_db: Db) -> None:
    assert mssql_db.rows("SELECT RegionCode, Name FROM dbo.Region ORDER BY RegionCode") == [
        {"RegionCode": "N1", "Name": "North"},
        {"RegionCode": "S1", "Name": "South"},
    ]


def test_finalize_ran_after_the_baseline_seed(mssql_db: Db) -> None:
    assert mssql_db.scalar("SELECT SeededRegions FROM dbo.FinalizeMarker") == 2


def test_csv_header_flag_suffix(tmp_path: Path, mssql_db: Db) -> None:
    # Both positional (RegionCode, Name); .1 skips one header row, whose text need not match the columns.
    (tmp_path / "dbo.Region.0.csv").write_text("E1,East\n", encoding="utf-8")
    (tmp_path / "dbo.Region.1.csv").write_text("Region Code,Region Name\nW1,West\n", encoding="utf-8")
    assert read_csv(tmp_path / "dbo.Region.0.csv")[0] == "dbo.Region"
    assert read_csv(tmp_path / "dbo.Region.1.csv")[0] == "dbo.Region"
    assert load_files(mssql_db, sorted(tmp_path.iterdir())) == 2
    assert mssql_db.rows("SELECT RegionCode, Name FROM dbo.Region WHERE RegionCode IN ('E1', 'W1') ORDER BY 1") == [
        {"RegionCode": "E1", "Name": "East"},
        {"RegionCode": "W1", "Name": "West"},
    ]


def test_csv_positional_too_many_values(tmp_path: Path, mssql_db: Db) -> None:
    (tmp_path / "dbo.Region.0.csv").write_text("E1,East,extra\n", encoding="utf-8")
    with pytest.raises(SeedError, match="3 values per row but only 2 columns"):
        load_files(mssql_db, [tmp_path / "dbo.Region.0.csv"])
