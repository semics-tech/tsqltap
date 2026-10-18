"""The other half of tests/sql/*.test.sql: every assertion must FAIL when it should, and say why."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from conftest import one, tap

from tsqltap.connection import BatchError, Db
from tsqltap.provision import Environment
from tsqltap.sqlfile import Assertion, parse_directives, run_sql_file

RunTap = Callable[[str], Assertion]


def test_ok_fails_on_0_and_null(mssql_db: Db) -> None:
    zero, null = tap(mssql_db, "EXEC tap.ok 0, N'zero'; EXEC tap.ok NULL, N'null';")
    assert not zero.ok
    assert not null.ok
    assert null.detail == "condition was NULL"


def test_is_reports_have_and_want(run_tap: RunTap) -> None:
    a = run_tap("EXEC tap.[is] 1, 2, N'numbers'")
    assert not a.ok
    assert a.detail == "have: '1' (int)\nwant: '2' (int)"


def test_is_compares_strings_exactly(run_tap: RunTap) -> None:
    assert not run_tap("EXEC tap.[is] N'abc', N'ABC'").ok
    assert not run_tap("EXEC tap.[is] 1, N'1'").ok  # no implicit conversion between families


def test_matches_reports_pattern(run_tap: RunTap) -> None:
    a = run_tap("EXEC tap.matches N'hello', N'bye%'")
    assert not a.ok
    assert "does not match: bye%" in (a.detail or "")


def test_results_eq_points_at_first_difference(run_tap: RunTap) -> None:
    a = run_tap(
        "EXEC tap.results_eq N'SELECT RegionCode, Name FROM dbo.Region ORDER BY RegionCode',"
        " N'VALUES (''N1'', N''North''), (''S1'', N''SOUTH'')'"
    )
    assert not a.ok
    assert a.detail == (
        "have 2 rows, want 2 rows\n"
        "first difference at row 2:\n"
        '  have: {"RegionCode":"S1","Name":"South"}\n'
        '  want: {"RegionCode":"S1","Name":"SOUTH"}'
    )


def test_results_eq_is_ordered(run_tap: RunTap) -> None:
    a = run_tap(
        "EXEC tap.results_eq N'SELECT RegionCode FROM dbo.Region ORDER BY RegionCode DESC', N'VALUES (''N1''), (''S1'')'"
    )
    assert not a.ok
    assert "first difference at row 1" in (a.detail or "")


def test_results_eq_missing_row(run_tap: RunTap) -> None:
    a = run_tap(
        "EXEC tap.results_eq N'SELECT RegionCode FROM dbo.Region WHERE RegionCode = ''N1''', N'VALUES (''N1''), (''S1'')'"
    )
    assert not a.ok
    assert "have: (no row)" in (a.detail or "")


def test_bag_eq_counts_duplicates(run_tap: RunTap) -> None:
    a = run_tap("EXEC tap.bag_eq N'SELECT RegionCode FROM dbo.Region', N'VALUES (''N1''), (''N1''), (''S1'')'")
    assert not a.ok
    assert 'missing (wanted, not returned): [{"RegionCode":"N1","__count":2}]' in (a.detail or "")
    assert 'extra (returned, not wanted): [{"RegionCode":"N1","__count":1}]' in (a.detail or "")


def test_set_eq_reports_missing_and_extra(run_tap: RunTap) -> None:
    a = run_tap("EXEC tap.set_eq N'SELECT RegionCode FROM dbo.Region', N'VALUES (''N1''), (''X9'')'")
    assert not a.ok
    assert '[{"RegionCode":"X9"}]' in (a.detail or "")
    assert '[{"RegionCode":"S1"}]' in (a.detail or "")


def test_is_empty_shows_rows(run_tap: RunTap) -> None:
    a = run_tap("EXEC tap.is_empty N'SELECT RegionCode FROM dbo.Region ORDER BY RegionCode'")
    assert not a.ok
    assert a.detail == 'have 2 rows, want 0 rows\nfirst rows: [{"RegionCode":"N1"},{"RegionCode":"S1"}]'


def test_compare_reports_bad_query(run_tap: RunTap) -> None:
    a = run_tap("EXEC tap.results_eq N'SELECT * FROM dbo.Nope', N'VALUES (1)'")
    assert not a.ok
    assert "could not determine the columns" in (a.detail or "")
    assert "dbo.Nope" in (a.detail or "")


def test_compare_reports_unconvertible_values(run_tap: RunTap, mssql_db: Db) -> None:
    a = run_tap("EXEC tap.results_eq N'SELECT 1 AS n', N'VALUES (''not a number'')'")
    assert not a.ok
    assert 'do not convert to the column types of the query under test: [{"n":"not a number"}]' in (a.detail or "")
    assert mssql_db.trancount == 1  # reported, not a transaction-dooming conversion error


def test_compare_column_count_mismatch(run_tap: RunTap) -> None:
    a = run_tap("EXEC tap.results_eq N'SELECT 1 AS a, 2 AS b', N'SELECT 1'")
    assert not a.ok
    assert a.detail == "the query under test returns 2 columns but the expected rows have 1"


def test_throws_ok_when_nothing_is_thrown(run_tap: RunTap) -> None:
    a = run_tap("EXEC tap.throws_ok N'SELECT 1'")
    assert not a.ok
    assert a.detail == "no error was raised"


def test_throws_ok_wrong_number(run_tap: RunTap) -> None:
    a = run_tap("EXEC tap.throws_ok N'SELECT 1/0', 547")
    assert not a.ok
    assert (a.detail or "").startswith("raised: 8134 Divide by zero")
    assert "wanted: 547" in (a.detail or "")


def test_throws_ok_undoes_partial_work(mssql_db: Db) -> None:
    a = one(
        mssql_db,
        "EXEC tap.throws_ok N'INSERT dbo.Region VALUES (''P1'', N''Partial''); SELECT 1/0;'",
    )
    assert a.ok
    assert mssql_db.scalar("SELECT COUNT(*) FROM dbo.Region WHERE RegionCode = 'P1'") == 0
    assert mssql_db.trancount == 1


def test_lives_ok_reports_the_error(run_tap: RunTap) -> None:
    a = run_tap("EXEC tap.lives_ok N'SELECT 1/0'")
    assert not a.ok
    assert (a.detail or "").startswith("raised: 8134")


def test_schema_failures(mssql_db: Db) -> None:
    results = tap(
        mssql_db,
        """
        EXEC tap.has_table N'dbo.Nope';
        EXEC tap.col_type_is N'dbo.Trust', N'Score', N'decimal(6,2)';
        EXEC tap.col_type_is N'dbo.Trust', N'Missing', N'int';
        EXEC tap.col_is_pk N'dbo.Trust', N'TrustCode';
        EXEC tap.has_permission N'ReaderUser', N'dbo.TrustAudit', N'SELECT';
        EXEC tap.has_permission N'NoSuchUser', N'dbo.Region', N'SELECT';
        """,
    )
    assert [r.ok for r in results] == [False] * 6
    assert results[0].description == "Table dbo.Nope should exist"
    assert results[1].detail == "have: decimal(5,2)\nwant: decimal(6,2)"
    assert results[2].detail == "have: (no such column)\nwant: int"
    assert results[3].detail == "have: (TrustId)"
    assert results[4].description == "ReaderUser should have SELECT on dbo.TrustAudit"
    assert results[5].detail == "no such database principal"


def test_permission_probe_user_is_cleaned_up(mssql_db: Db) -> None:
    assert one(mssql_db, "EXEC tap.has_permission N'ReportingRole', N'dbo.Region', N'SELECT'").ok
    assert mssql_db.scalar("SELECT COUNT(*) FROM sys.database_principals WHERE name LIKE 'tap_probe_%'") == 0


def test_mocks_refuse_to_run_outside_a_transaction(mssql_env: Environment) -> None:
    with mssql_env.connect("TsqltapSelf") as db:
        for call in ("EXEC tap.fake_table N'dbo.Region'", "EXEC tap.spy_procedure N'dbo.usp_Notify'"):
            try:
                db.run(call)
            except BatchError as exc:
                assert "needs an open transaction" in exc.message
            else:
                raise AssertionError(f"{call} ran outside a transaction")


# ---------------------------------------------------------------------------- whole files


def _run(env: Environment, tmp_path: Path, name: str, sql: str):  # type: ignore[no-untyped-def]
    path = tmp_path / name
    path.write_text(sql, encoding="utf-8")
    with env.connect("TsqltapSelf") as db:
        return run_sql_file(db, path, parse_directives(sql, name), env.cfg.root)


def test_file_error_after_assertions(mssql_env: Environment, tmp_path: Path) -> None:
    r = _run(
        mssql_env,
        tmp_path,
        "e.test.sql",
        "EXEC tap.pass N'first';\nGO\nSELECT * FROM dbo.Nope;\nGO\nEXEC tap.pass N'never';",
    )
    assert [a.description for a in r.assertions] == ["first"]
    assert r.error is not None and "dbo.Nope" in r.error


def test_file_isolation_broken_by_rollback(mssql_env: Environment, tmp_path: Path) -> None:
    r = _run(
        mssql_env,
        tmp_path,
        "rb.test.sql",
        "EXEC dbo.usp_AddRegionOrRollBack @RegionCode = 'Q1', @Name = N'bad';\nEXEC tap.pass N'after';",
    )
    assert r.isolation_broken
    assert r.failed


def test_legacy_failure_is_not_an_error(mssql_env: Environment, tmp_path: Path) -> None:
    r = _run(
        mssql_env,
        tmp_path,
        "L.Tests.sql",
        "SELECT 1 AS Seq, 'good' AS [Check], 'PASS' AS Result, NULL AS Detail"
        " UNION ALL SELECT 2, 'bad', 'FAIL', 'it broke';\n"
        "THROW 50000, 'one or more checks failed', 1;",
    )
    assert r.legacy
    assert r.error is None
    assert [(a.description, a.ok, a.detail) for a in r.assertions] == [("good", True, None), ("bad", False, "it broke")]


def test_legacy_all_pass_then_error_is_an_error(mssql_env: Environment, tmp_path: Path) -> None:
    r = _run(
        mssql_env,
        tmp_path,
        "L2.Tests.sql",
        "SELECT 'good' AS [Check], 'PASS' AS Result, NULL AS Detail; SELECT * FROM dbo.Nope;",
    )
    assert r.error is not None


def test_tsqlt_failure_is_reported(mssql_env: Environment, tmp_path: Path) -> None:
    sql = (
        "-- tsqltap: tsqlt=all; isolation=transaction\n"
        "EXEC (N'CREATE SCHEMA tSQLt');\nGO\n"
        "CREATE TABLE tSQLt.TestResult (Id INT IDENTITY, Class NVARCHAR(200), TestCase NVARCHAR(200), Result NVARCHAR(20), Msg NVARCHAR(MAX));\nGO\n"
        "CREATE PROCEDURE tSQLt.RunAll AS INSERT tSQLt.TestResult VALUES (N'C', N'test one', N'Failure', N'Expected 1 but was 2');"
        " RAISERROR (N'1 failed', 16, 1);\nGO\n"
    )
    r = _run(mssql_env, tmp_path, "t.test.sql", sql)
    assert r.error is None
    assert [(a.description, a.ok, a.detail) for a in r.assertions] == [("C.test one", False, "Expected 1 but was 2")]


def test_missing_tsqlt_is_an_error(mssql_env: Environment, tmp_path: Path) -> None:
    r = _run(mssql_env, tmp_path, "t2.test.sql", "-- tsqltap: tsqlt=all\nSELECT 1;")
    assert r.error is not None and "tSQLt is not installed" in r.error


def test_todo_rows(mssql_env: Environment, tmp_path: Path) -> None:
    r = _run(
        mssql_env,
        tmp_path,
        "todo.test.sql",
        "EXEC tap.todo_start N'why';\nEXEC tap.fail N'still broken';\nEXEC tap.pass N'fixed already';\n"
        "EXEC tap.todo_end;\nEXEC tap.pass N'normal';",
    )
    assert [(a.description, a.ok, a.todo) for a in r.assertions] == [
        ("still broken", False, "why"),
        ("fixed already", True, "why"),
        ("normal", True, None),
    ]
    assert r.failed  # the passing TODO


def test_lives_ok_rolls_back_a_transaction_the_code_left_open(mssql_env: Environment) -> None:
    with mssql_env.connect("TsqltapSelf") as db:
        a = one(db, "EXEC tap.lives_ok N'EXEC dbo.usp_OpensTransactionThenThrows'")
        assert not a.ok
        assert "50999" in (a.detail or "")
        assert db.trancount == 0
