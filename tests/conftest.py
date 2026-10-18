from __future__ import annotations

import pytest

from tsqltap.connection import BatchError, Db
from tsqltap.sqlfile import Assertion, FileResult, collect_results


def tap(db: Db, sql: str) -> list[Assertion]:
    """Run tap calls and return the assertions they emitted."""
    result = FileResult()
    try:
        collect_results(result, db.run(sql))
    except BatchError as exc:
        collect_results(result, exc.results)
        raise
    return result.assertions


def one(db: Db, sql: str) -> Assertion:
    assertions = tap(db, sql)
    assert len(assertions) == 1, assertions
    return assertions[0]


@pytest.fixture
def run_tap(mssql_db: Db):  # type: ignore[no-untyped-def]
    return lambda sql: one(mssql_db, sql)
