"""Run one SQL test file and collect its assertions.

A SQL test file is plain T-SQL, split into batches on `GO` lines and run on one connection. Header
directives control how it runs:

    -- tsqltap: isolation=snapshot      transaction (default) | snapshot | none
    -- tsqltap: database=Reporting      which configured database to run in
    -- tsqltap: requires=clr, agent     skip unless the server has these capabilities
    -- tsqltap: seed=fixtures/a.csv     load a seed file first (repeatable; relative to this file)
    -- tsqltap: skip=reason            skip the file
    -- tsqltap: restore=Reporting       with isolation=snapshot: also reset these databases afterwards
                                          (for tests that commit data in more than one database)
    -- tsqltap: tsqlt=all               then run tSQLt.RunAll (or tsqlt=<TestClass>) and report
                                          each tSQLt test case as an assertion

Assertions come back as result sets: `tap.*` rows (`__tap` column), or -- for scripts written
before tsqltap -- rows with `Check`, `Result` (PASS/FAIL) and `Detail` columns.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from pathlib import Path

from tsqltap.connection import BatchError, Db, ResultSet

ISOLATIONS = ("transaction", "snapshot", "none")
LEGACY_GLOB = "*.Tests.sql"

_DIRECTIVE = re.compile(r"^\s*--\s*tsqltap:\s*(?P<body>.+?)\s*$", re.IGNORECASE | re.MULTILINE)
_GO = re.compile(r"^\s*GO\s*(?:--.*)?$", re.IGNORECASE | re.MULTILINE)


class DirectiveError(ValueError):
    pass


@dataclass
class Directives:
    isolation: str = "transaction"
    database: str | None = None
    requires: list[str] = field(default_factory=list)
    seeds: list[str] = field(default_factory=list)
    skip: str | None = None
    tsqlt: str | None = None
    restore: list[str] = field(default_factory=list)


def parse_directives(text: str, filename: str = "") -> Directives:
    """Read `-- tsqltap: key=value` lines. Several pairs may share a line, separated by `;`."""
    d = Directives()
    # Scripts that predate tsqltap manage their own transaction; wrapping them in ours would make
    # their ROLLBACK end it early.
    if fnmatch.fnmatch(filename, LEGACY_GLOB):
        d.isolation = "none"
    explicit_isolation = False
    for m in _DIRECTIVE.finditer(text):
        for pair in m.group("body").split(";"):
            if not pair.strip():
                continue
            key, sep, value = pair.partition("=")
            key, value = key.strip().lower(), value.strip()
            if not sep:
                raise DirectiveError(f"expected key=value in directive: {pair.strip()!r}")
            if key == "isolation":
                if value not in ISOLATIONS:
                    raise DirectiveError(f"isolation must be one of {ISOLATIONS}, not {value!r}")
                d.isolation = value
                explicit_isolation = True
            elif key == "database":
                d.database = value
            elif key == "requires":
                d.requires.extend(v.strip().lower() for v in value.split(",") if v.strip())
            elif key == "seed":
                d.seeds.append(value)
            elif key == "skip":
                d.skip = value or "skipped by directive"
            elif key == "restore":
                d.restore.extend(v.strip() for v in value.split(",") if v.strip())
            elif key == "tsqlt":
                d.tsqlt = value or "all"
            else:
                raise DirectiveError(f"unknown directive {key!r}")
    if d.restore and d.isolation != "snapshot":
        raise DirectiveError("restore= only applies with isolation=snapshot")
    # tSQLt runs every test case in its own transaction and rolls it back itself.
    if d.tsqlt and not explicit_isolation:
        d.isolation = "none"
    return d


def split_batches(text: str) -> list[str]:
    return [b.strip() for b in _GO.split(text) if b.strip()]


@dataclass
class Assertion:
    description: str
    ok: bool
    detail: str | None = None
    todo: str | None = None  # set: a known failure (tap.todo_start), with the reason


@dataclass
class FileResult:
    assertions: list[Assertion] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)
    error: str | None = None
    skipped: str | None = None
    isolation_broken: bool = False
    doomed: bool = False  # isolation=transaction ended with XACT_STATE() = -1: uncommittable
    legacy: bool = False

    @property
    def failed(self) -> bool:
        return (
            self.error is not None
            or self.isolation_broken
            or self.doomed
            or any(not a.ok if a.todo is None else a.ok for a in self.assertions)
        )


def _get(row: dict[str, object], name: str) -> object:
    for k, v in row.items():
        if k.lower() == name:
            return v
    return None


def collect_results(result: FileResult, sets: list[ResultSet]) -> None:
    for rs in sets:
        if rs.has_columns("__tap"):
            for row in rs.rows:
                kind = str(_get(row, "kind") or "assert")
                description = str(_get(row, "description") or "")
                detail = _get(row, "detail")
                if kind == "diag":
                    result.diagnostics.append(str(detail))
                elif kind == "skip":
                    result.skipped = description or "skipped"
                else:
                    todo = _get(row, "todo") if kind == "todo" else None
                    result.assertions.append(
                        Assertion(
                            description,
                            bool(_get(row, "ok")),
                            None if detail is None else str(detail),
                            None if todo is None else str(todo),
                        )
                    )
        elif rs.has_columns("check", "result", "detail"):
            result.legacy = True
            for row in rs.rows:
                detail = _get(row, "detail")
                result.assertions.append(
                    Assertion(
                        str(_get(row, "check")),
                        str(_get(row, "result")).upper() == "PASS",
                        None if detail is None else str(detail),
                    )
                )


DOOMED_MESSAGE = (
    "the test left the transaction it was wrapped in uncommittable (XACT_STATE() = -1), so it could "
    "not be used any further. Mark the file `-- tsqltap: isolation=snapshot`. Common causes: an "
    "XACT_ABORT ON procedure that throws, a THROW inside a trigger, a denied TRUNCATE, a failing "
    "ALTER PARTITION FUNCTION ... MERGE RANGE."
)


def run_sql_file(db: Db, path: Path, directives: Directives, root: Path | None = None) -> FileResult:
    """Run the file's batches in order and return what it asserted.

    isolation=transaction wraps everything (seeds included) in one transaction that is always
    rolled back. If the code under test ends that transaction itself, the file fails with
    `isolation_broken`: its changes would otherwise leak into the next test.
    """
    from tsqltap.seed import SeedError, load_files, resolve_seed_paths

    result = FileResult()
    batches = split_batches(path.read_text(encoding="utf-8-sig"))
    wrapped = directives.isolation == "transaction"

    if wrapped:
        db.execute("BEGIN TRANSACTION")
    try:
        if directives.seeds:
            try:
                load_files(db, resolve_seed_paths(directives.seeds, path.parent, root))
            except (BatchError, SeedError) as exc:
                result.error = f"loading seed data failed: {exc}"
                return result

        for batch in batches:
            try:
                sets = db.run(batch)
            except BatchError as exc:
                collect_results(result, exc.results)
                # A pre-tsqltap script THROWs after its results when a check failed: that is
                # its way of failing a bare `sqlcmd -b` run, not an error in the test.
                expected = result.legacy and any(not a.ok for a in result.assertions)
                if not expected:
                    result.error = exc.message
                # A procedure that rolls back raises 266 (transaction count mismatch) on the way out.
                if wrapped and db.trancount == 0:
                    if "uncommittable transaction" in exc.message.lower():
                        # SQL Server rolled the doomed transaction back at the end of the batch (3998).
                        result.doomed = True
                        result.error = DOOMED_MESSAGE
                    else:
                        result.isolation_broken = True
                break
            collect_results(result, sets)
            if wrapped and db.trancount == 0:
                result.isolation_broken = True
                break

        if wrapped and not result.isolation_broken and db.trancount > 0 and db.scalar("SELECT XACT_STATE()") == -1:
            result.doomed = True
            result.error = DOOMED_MESSAGE + (f"\nThe last error was: {result.error}" if result.error else "")

        if directives.tsqlt and result.error is None and not result.isolation_broken:
            run_tsqlt(db, directives.tsqlt, result)
    finally:
        if db.trancount > 0:
            db.execute("WHILE @@TRANCOUNT > 0 ROLLBACK TRANSACTION")

    return result


TSQLT_OK = {"success", "skipped"}


def run_tsqlt(db: Db, target: str, result: FileResult) -> None:
    """Run tSQLt and turn tSQLt.TestResult into assertions, one per test case."""
    run = "EXEC tSQLt.RunAll" if target.lower() == "all" else "EXEC tSQLt.Run @TestName = ?"
    try:
        db.run(run, () if target.lower() == "all" else (target,))
    except BatchError as exc:
        # tSQLt raises at the end of a run with failures; the per-test results are in the table.
        if db.scalar("SELECT OBJECT_ID(N'tSQLt.TestResult')") is None:
            result.error = f"tSQLt is not installed in this database ({exc.message})"
            return
    for row in db.rows("SELECT Class, TestCase, Result, Msg FROM tSQLt.TestResult ORDER BY Id"):
        status = str(row["Result"]).lower()
        detail = row["Msg"] if status != "skipped" else f"skipped: {row['Msg']}"
        result.assertions.append(
            Assertion(f"{row['Class']}.{row['TestCase']}", status in TSQLT_OK, None if detail is None else str(detail))
        )
