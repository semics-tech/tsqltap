"""pytest plugin: collects SQL test files and provides fixtures for Python tests.

SQL files (`*.test.sql`, and `*.Tests.sql` from before tsqltap) become one test each; every
assertion inside one is reported as a subtest, and in JUnit XML as its own <testcase>.

Python tests use the fixtures:

    def test_active_notification(mssql_db):
        mssql_db.seed("App.Notification", [{"Title": "x", "IsActive": 1}])
        assert mssql_db.scalar("SELECT COUNT(*) FROM App.vw_ActiveNotification") == 1

    @pytest.mark.mssql(database="Reporting", isolation="snapshot", requires=["clr"])
    def test_commits(mssql_db): ...
"""

from __future__ import annotations

import fnmatch
from collections import Counter
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from tsqltap.config import Config, ConfigError, load
from tsqltap.connection import BatchError, Db
from tsqltap.provision import Environment
from tsqltap.sqlfile import DirectiveError, Directives, parse_directives, run_sql_file

_CONFIG = pytest.StashKey[Config]()
# Node ids of collected SQL tests. Module-level because pytest_runtest_logreport gets no config
# (and reports from xdist workers arrive in the controller, which collected the same ids).
_sql_item_ids: set[str] = set()


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("tsqltap", "SQL Server database tests (tsqltap)")
    group.addoption(
        "--tsqltap-keep",
        action="store_true",
        default=False,
        help="leave the SQL Server container running after the session, and reuse it (and any "
        "unchanged database) next time. Remove it with `tsqltap down`.",
    )
    group.addoption("--tsqltap-image", default=None, help="SQL Server image to use (overrides config)")


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "mssql(database=None, isolation='transaction', requires=(), restore=()): which configured database a "
        "test uses, how it is isolated (transaction | snapshot | none), which server capabilities it needs, "
        "and which other databases to reset after a snapshot-isolated test",
    )
    cfg = load(config.rootpath)
    if config.getoption("--tsqltap-image", None):
        cfg.image = config.getoption("--tsqltap-image")
    config.stash[_CONFIG] = cfg
    _check_tests_folders_are_collected(config, cfg)


def _check_tests_folders_are_collected(config: pytest.Config, cfg: Config) -> None:
    """Refuse to run if a database's `tests` folder would be skipped by norecursedirs.

    pytest's default norecursedirs includes "build" -- matched case-insensitively on Windows -- so a
    folder such as Tests/Build would otherwise be skipped without a word, and the run would simply be
    smaller than it should be.
    """
    patterns = [str(p) for p in config.getini("norecursedirs")]
    for db in cfg.databases:
        for glob_ in db.tests:
            for part in Path(glob_.replace("\\", "/")).parts:
                if any(c in part for c in "*?["):
                    break
                hit = next((p for p in patterns if fnmatch.fnmatch(part.lower(), p.lower())), None)
                if hit:
                    raise pytest.UsageError(
                        f"[tool.tsqltap] database {db.name!r} has tests under {glob_!r}, but pytest's norecursedirs "
                        f"pattern {hit!r} matches the folder {part!r}, so they would be skipped silently. Set "
                        f"norecursedirs in [tool.pytest.ini_options] without it."
                    )


def pytest_collect_file(file_path: Path, parent: pytest.Collector) -> pytest.Collector | None:
    cfg = parent.config.stash[_CONFIG]
    if any(fnmatch.fnmatch(file_path.name, g) for g in cfg.sql_globs):
        return SqlFile.from_parent(parent, path=file_path)
    return None


class SqlFile(pytest.File):
    def collect(self) -> Iterator[pytest.Item]:
        text = self.path.read_text(encoding="utf-8-sig")
        try:
            directives = parse_directives(text, self.path.name)
        except DirectiveError as exc:
            raise self.CollectError(f"{self.path.name}: {exc}") from exc

        name = self.path.name.split(".")[0]
        item = pytest.Function.from_parent(self, name=name, callobj=_sql_test(self.path, directives))
        if directives.skip:
            item.add_marker(pytest.mark.skip(reason=directives.skip))
        _sql_item_ids.add(item.nodeid)
        yield item

    def reportinfo(self) -> tuple[Path, int, str]:
        return self.path, 0, self.path.name


def _unique(names: list[str]) -> list[str]:
    seen: Counter[str] = Counter()
    out = []
    for n in names:
        n = n or "(no description)"
        seen[n] += 1
        out.append(n if seen[n] == 1 else f"{n} ({seen[n]})")
    return out


def _sql_test(path: Path, directives: Directives) -> Callable[..., None]:
    def run_sql_test(subtests: Any, mssql_env: Environment) -> None:
        __tracebackhide__ = True
        reason = mssql_env.check_requires(directives.requires)
        if reason:
            pytest.skip(reason)
        dbname = mssql_env.cfg.database_for(path, directives.database).name

        commits = directives.isolation != "transaction"
        if commits:
            mssql_env.mark_dirty(dbname, *directives.restore)
        db = mssql_env.lease(dbname)
        result = None
        try:
            result = run_sql_file(db, path, directives, mssql_env.cfg.root)
        finally:
            clean = result is not None and not commits and not result.isolation_broken
            mssql_env.release(db, reusable=clean)
            if directives.isolation == "snapshot":
                for name in dict.fromkeys([dbname, *directives.restore]):
                    mssql_env.restore(mssql_env.cfg.database(name).name)
        assert result is not None

        if result.isolation_broken and directives.isolation != "snapshot":
            mssql_env.restore(dbname)  # the test's changes escaped; don't let the next test see them
        for line in result.diagnostics:
            print(f"# {line}")
        if result.skipped:
            pytest.skip(result.skipped)

        names = _unique([a.description for a in result.assertions])
        for name, assertion in zip(names, result.assertions, strict=True):
            with subtests.test(name):
                if assertion.todo is not None:
                    if assertion.ok:
                        pytest.fail(
                            f"TODO passed: {assertion.todo}\nThe known failure is fixed: remove its "
                            "tap.todo_start / tap.todo_end so this assertion guards the fix.",
                            pytrace=False,
                        )
                    pytest.xfail(f"TODO: {assertion.todo}" + (f"\n{assertion.detail}" if assertion.detail else ""))
                if not assertion.ok:
                    pytest.fail(assertion.detail or "failed", pytrace=False)

        problems = []
        if result.error:
            problems.append(f"SQL error: {result.error}")
        if result.isolation_broken:
            problems.append(
                "the test ended the transaction it was wrapped in (a COMMIT or ROLLBACK in the code under "
                "test), so its changes could not be undone that way; the database was reset from its "
                "snapshot. Mark the file `-- tsqltap: isolation=snapshot`."
            )
        if not result.assertions and not problems:
            problems.append("no assertions ran")
        if problems:
            pytest.fail("\n".join(problems), pytrace=False)

    run_sql_test.__doc__ = f"SQL test file {path.name}"
    return run_sql_test


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    """Give each SQL assertion its own node id, so JUnit XML gets one <testcase> per assertion."""
    context = getattr(report, "context", None)
    msg = getattr(context, "msg", None)
    if msg is not None and report.nodeid in _sql_item_ids:
        report.nodeid = f"{report.nodeid}::{msg}"


# ---------------------------------------------------------------------------- fixtures


@pytest.fixture(scope="session")
def mssql_env(request: pytest.FixtureRequest) -> Iterator[Environment]:
    """The SQL Server container, with every configured database published, seeded and snapshotted."""
    cfg = request.config.stash[_CONFIG]
    if not cfg.databases:
        raise ConfigError(f"no [[tool.tsqltap.databases]] in {cfg.root / 'pyproject.toml'}")
    env = Environment(cfg, keep=request.config.getoption("--tsqltap-keep"))
    env.start()
    try:
        env.provision()
        yield env
    finally:
        env.stop()


@pytest.fixture
def mssql_db(request: pytest.FixtureRequest, mssql_env: Environment) -> Iterator[Db]:
    """A connection to the test's database, isolated per the `mssql` marker (default: a transaction
    that is rolled back after the test)."""
    marker = request.node.get_closest_marker("mssql")
    opts = dict(marker.kwargs) if marker else {}
    isolation = opts.get("isolation", "transaction")
    if isolation not in ("transaction", "snapshot", "none"):
        raise ValueError(f"mssql marker: unknown isolation {isolation!r}")
    reason = mssql_env.check_requires(tuple(opts.get("requires", ())))
    if reason:
        pytest.skip(reason)
    name = mssql_env.cfg.database_for(request.node.path, opts.get("database")).name

    if isolation != "transaction":
        mssql_env.mark_dirty(name, *opts.get("restore", ()))
    db = mssql_env.lease(name)
    broken = False
    clean = False
    try:
        if isolation == "transaction":
            db.execute("BEGIN TRANSACTION")
        yield db
        broken = isolation == "transaction" and db.trancount == 0
    finally:
        try:
            db.execute("WHILE @@TRANCOUNT > 0 ROLLBACK TRANSACTION")
            clean = isolation == "transaction" and not broken
        except BatchError:
            pass  # the connection is unusable; release() closes it
        finally:
            mssql_env.release(db, reusable=clean)
        if isolation == "snapshot" or broken:
            for other in dict.fromkeys([name, *opts.get("restore", ())]):
                mssql_env.restore(mssql_env.cfg.database(other).name)
    if broken:
        pytest.fail(
            "the test ended the transaction it was wrapped in, so its changes could not be rolled back "
            "(the database was reset from its snapshot); use @pytest.mark.mssql(isolation='snapshot')",
            pytrace=False,
        )
