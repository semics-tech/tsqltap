# tsqltap

pgTAP-style testing for SQL Server. You write assertions in T-SQL, pytest runs them, and every
run gets a disposable SQL Server container.

tSQLt isn't well supported anymore and feels clunky and short on features. tsqltap is a SQL Server
testing tool in the mould of pgTAP, which is simple and proven.

```sql
-- tests/sql/notification.test.sql
INSERT App.Notification (Title, Body, [Type], IsActive) VALUES (N'current', N'x', 'info', 1);

EXEC tap.results_eq N'SELECT Title FROM App.vw_ActiveNotification', N'VALUES (N''current'')',
     N'the newest active notification wins';
EXEC tap.throws_ok N'INSERT App.Notification ([Type]) VALUES (''bogus'')', 547, NULL,
     N'Type CHECK rejects unknown types';
```

```console
$ uv run pytest
tests/sql/notification.test.sql::notification ,,.          [100%]
```

`tap` itself is pure T-SQL; pytest is only the runner. You can install `tap` into any database and
use it from SSMS or `sqlcmd` without Python: see
[Use tap without Python](https://github.com/semics-tech/tsqltap/blob/main/docs/use-without-python.md).

## Quickstart (about 10 minutes)

You need Python ≥ 3.12, uv and a running Docker. No dacpac is needed: this starts from an empty
database built by a script.

```console
$ mkdir tsqltap-demo && cd tsqltap-demo
$ uv init --bare && uv add --dev tsqltap
```

Add to `pyproject.toml`:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]

[[tool.tsqltap.databases]]
name    = "Demo"
scripts = ["tests/setup/*.sql"]
```

Create `tests/setup/01_schema.sql`:

```sql
CREATE TABLE dbo.Person (Id INT IDENTITY PRIMARY KEY, Name NVARCHAR(50) NOT NULL);
```

Create the first test with `uv run tsqltap new tests/sql/person`, then replace its placeholder
assertion with:

```sql
INSERT dbo.Person (Name) VALUES (N'Ada');
EXEC tap.row_count_is N'SELECT * FROM dbo.Person', 1, N'one person';
```

```console
$ uv run pytest
```

The first run pulls the SQL Server image (a few minutes, once), starts a container, builds
`Demo` and runs the test. Add `--tsqltap-keep` to keep the container and make later runs fast.
To test a real project, point `dacpac` at your built `.dacpac` (see
[Setup](#setup-in-a-database-repo)).

## How it compares

| | tsqltap | tSQLt | pgTAP |
|---|---|---|---|
| Database | SQL Server | SQL Server | PostgreSQL |
| Tests written in | T-SQL | T-SQL | SQL |
| Runner | pytest (`*.test.sql` plus Python tests) | `tSQLt.Run*` inside the database | `pg_prove` |
| Library lives in | the test database only, never your dacpac | the database under test | the database under test |
| Environment | disposable container per run | you provide the server | you provide the server |
| CI reporting | JUnit, one `<testcase>` per assertion | via a wrapper | TAP output |

tsqltap borrows pgTAP's style (`ok`, `is`, `results_eq`, `throws_ok`, TODO blocks) and offers
tSQLt-style mocking (`fake_table`, `spy_procedure`). An existing tSQLt suite can run through an
experimental bridge (see [Existing tests](#existing-tests)), so you can migrate one class at a time.

## How it works

- **SQL test files** (`*.test.sql`) are plain T-SQL. Assertions are procedures in a `tap` schema.
  Each assertion streams one result row back to the runner, so its result survives a `ROLLBACK`
  inside the code under test.
- **The pytest plugin** collects those files, plus ordinary Python tests.
  - Each SQL file is one test, and each assertion in it is a subtest.
  - In JUnit XML (`--junitxml`, which CI systems read), every assertion is its own `<testcase>`.
- **A SQL Server 2025 container** is started with [testcontainers] for the session. Your
  dacpacs are published into it with `sqlpackage`, setup scripts run, `tap` is installed, and
  baseline seed data is loaded. Then a **database snapshot** is taken.
- **Isolation:** every test runs in a transaction that is rolled back. For code that commits or
  rolls back itself, mark the test `isolation=snapshot`; the database is then restored from the
  snapshot afterwards.

`tap` is installed only into the test database. It is never part of your dacpac.

## Requirements

- Python ≥ 3.12 and [uv](https://docs.astral.sh/uv/)
- Docker (Docker Desktop on Windows)
- `sqlpackage` on PATH if you publish dacpacs: `dotnet tool install -g microsoft.sqlpackage`
- A built dacpac. Classic SSDT projects build with MSBuild on Windows; tsqltap doesn't build them.

No ODBC driver is needed, because [mssql-python] bundles its own.

## Install

```console
$ uv add --dev tsqltap      # or: pip install tsqltap
```

## Setup in a database repo

```toml
# pyproject.toml
[dependency-groups]
dev = ["tsqltap"]          # or: uv add --dev tsqltap

[tool.pytest.ini_options]
testpaths = ["tests"]

[tool.tsqltap]
image = "mcr.microsoft.com/mssql/server:2025-CU9-ubuntu-24.04"   # the default; pin your own
collation = "Latin1_General_CI_AS"     # server/tempdb collation: match production (see below)

[[tool.tsqltap.databases]]            # built in this order; the first is the default
name    = "Shop"
dacpac  = "src/Shop/bin/Release/Shop.dacpac"
scripts = ["tests/setup/*.sql"]         # optional, run after publish, sorted
seed    = ["tests/seed/baseline/*.csv"] # optional, loaded once, part of the snapshot
finalize = ["tests/setup/merge.sql"]   # optional, run after the seed, before the snapshot
tests   = ["src/Shop/Tests"]          # optional: test files under here default to this database
variables = { Environment = "test" }    # SQLCMD variables (/v:)
publish   = { ExcludeObjectTypes = "Users;Logins;RoleMembership" }   # extra /p: properties
```

**Config keys:**
- **Databases:** a database with no `dacpac` is created empty, then built from `scripts`.
- **Publish defaults:** `CreateNewDatabase=True`, `BlockOnPossibleDataLoss=False` and
  `AllowIncompatiblePlatform=True` (so Azure SQL projects publish to the container).
- **Other keys:** `password` (default: random per session; a kept container uses `Tsqltap!Passw0rd`
  and is bound to 127.0.0.1), `edition` (default `Developer`), `sql_globs` (default
  `*.test.sql`, `*.Tests.sql`), and `capabilities`.
- **Query Store:** turned off in each database before its snapshot is taken. SQL Server 2022 and
  later enable it on every new database, and its background flush makes each snapshot restore wait
  about 3 seconds. Set `query_store = true` on a database whose tests need it.
- **Server setup:** `setup = ["tests/setup/server/*.sql"]` runs scripts in `master` once the container
is up and before any database is built: logins, linked servers, server options. They run on every
session, including a reused container, so write them to be re-runnable (`IF NOT EXISTS ...`). Changing
them republishes every database.

**Collation:** set `collation` to your production databases' collation. The container's server
default is `SQL_Latin1_General_CP1_CI_AS`, and temp tables take `tempdb`'s collation. So code that
compares temp-table columns with database columns fails with "Cannot resolve the collation
conflict" when the two differ.

**Environment overrides:** `TSQLTAP_IMAGE`, `TSQLTAP_COLLATION`, `TSQLTAP_PASSWORD`, `TSQLTAP_EDITION`, `TSQLTAP_SQLPACKAGE`, `TSQLTAP_PUBLISH_TIMEOUT` (seconds a dacpac publish may take, default 1800; also `publish_timeout` in `[tool.tsqltap]`).

**Test folders named `build`:** pytest's default `norecursedirs` skips any folder named `build`,
matched case-insensitively on Windows, so `Tests/Build` would be silently left out. tsqltap refuses to
run if a database's `tests` folder would be skipped this way. Set `norecursedirs` in
`[tool.pytest.ini_options]` without `build`, e.g.
`[".*", "*.egg", "dist", "node_modules", "venv", "bin", "obj"]`.

**Which database a test runs in:**
1. the one it names, with a `database=` directive or `@pytest.mark.mssql(database=...)`;
2. otherwise the database whose `tests` globs match the file's path; if several match, the
   longest pattern wins;
3. otherwise the first database listed.

So in a repo with several projects, give each database its tests folder, and tests need no
directive.

**Cross-database projects:** list every database the project references, in dependency order.
They all go in the same container, so three-part names resolve. Pass the database names as
SQLCMD variables in `variables`.

## Writing SQL tests

Create `tests/sql/<name>.test.sql` (`tsqltap new tests/sql/<name>` scaffolds one). Batches are
split on `GO` and run on one connection. Optional header directives:

| Directive | Meaning |
|---|---|
| `-- tsqltap: isolation=transaction` | default: wrap the file in a transaction and roll it back |
| `-- tsqltap: isolation=snapshot` | no wrapper; restore the database from its snapshot afterwards (for code that commits, rolls back, or does DDL such as partition `SWITCH`) |
| `-- tsqltap: isolation=none` | no wrapper and no reset: you clean up |
| `-- tsqltap: database=Reporting` | run in this configured database (overrides the folder default) |
| `-- tsqltap: requires=clr, agent` | skip unless the server has these capabilities |
| `-- tsqltap: seed=fixtures/trusts.yaml` | load seed data first, inside the transaction (repeatable; paths relative to the file, then the project root) |
| `-- tsqltap: skip=reason` | skip the file |
| `-- tsqltap: restore=Reporting, ...` | with `isolation=snapshot`: also reset these databases afterwards, for tests that commit data in more than one database (the Python marker takes `restore=[...]`) |
| `-- tsqltap: tsqlt=all` | then run `tSQLt.RunAll` (or `tsqlt=<TestClass>`); see below |

Several directives can share a line if you separate them with `;`.

If the code under test ends the wrapping transaction, the file fails and the database is reset
from its snapshot. The failure tells you to use `isolation=snapshot`.

### Assertions

Every assertion takes an optional last `@description`. The default description says what was checked.

| Assertion | Passes when |
|---|---|
| `tap.ok @condition` | `@condition = 1` |
| `tap.[is] @have, @want` | equal, NULL-safe; strings compare exactly (case-sensitive) |
| `tap.isnt @have, @want` | not equal |
| `tap.matches @have, @pattern` | `@have LIKE @pattern` |
| `tap.pass` / `tap.fail [@detail]` | always / never |
| `tap.results_eq @have, @want` | same rows **in the same order** |
| `tap.bag_eq @have, @want` | same rows, any order, duplicates counted |
| `tap.set_eq @have, @want` | same distinct rows, any order |
| `tap.is_empty @sql` | no rows |
| `tap.row_count_is @sql, @count` | exactly `@count` rows |
| `tap.throws_ok @sql [, @errnum] [, @msg_like]` | `@sql` raises, optionally a given error number and/or message pattern |
| `tap.lives_ok @sql` | `@sql` doesn't raise |
| `tap.has_table`, `has_view`, `has_proc`, `has_function` (and `hasnt_*`) | the object exists (or doesn't) |
| `tap.has_schema`, `has_trigger @table, @trigger` | it exists |
| `tap.has_column` / `hasnt_column @table, @column` | the column exists (or doesn't) |
| `tap.col_type_is @table, @column, 'decimal(5,2)'` | the column's type, written as in DDL |
| `tap.col_not_null`, `col_is_null`, `col_has_default`, `col_is_identity` | the column property holds |
| `tap.has_pk`, `col_is_pk @table, 'A,B'`, `has_fk @table [, @references]`, `has_index`, `has_check` | the key, index or constraint exists |
| `tap.has_permission` / `hasnt_permission @principal, @securable, @permission` | the user or role has (or lacks) the permission, as SQL Server evaluates it (DENY, role membership); `@securable` NULL means the database |
| `tap.diag @message` | never fails; printed with the test output |
| `tap.skip_all @reason` | marks the whole file skipped |
| `tap.todo_start @why` … `tap.todo_end` | assertions in between are **known failures** (pgTAP's TODO). A failing one is reported as an expected failure (xfail) and doesn't fail the run. A passing one **does** fail, saying the bug is fixed, so you remove the TODO and the test guards the fix. |

**Arguments to the result comparisons:**
- `@have` is a query: a `SELECT`, or an `EXEC` of a procedure whose first result set can be described.
- `@want` is a query or a bare `VALUES (...), (...)` list.
- Failure detail shows the first differing row, or the missing and extra rows, as JSON:

```
have 2 rows, want 2 rows
first difference at row 2:
  have: {"RegionCode":"S1","Name":"South"}
  want: {"RegionCode":"S1","Name":"SOUTH"}
```

`throws_ok` runs `@sql` under a savepoint with `XACT_ABORT OFF`. An expected constraint violation
neither leaves partial work behind nor dooms the test's transaction.

T-SQL only accepts variables and literals as `EXEC` arguments, so compute expressions into
variables first:

```sql
DECLARE @n INT = (SELECT COUNT(*) FROM dbo.Trust);
EXEC tap.[is] @n, 3, N'three trusts';
```

### Mocking

Both mocks are DDL, which the test's transaction undoes. Both refuse to run outside a transaction.

- **`EXEC tap.fake_table N'dbo.Trust'`** swaps the table for an empty copy.
  - Unlike `tSQLt.FakeTable`, it keeps CHECK, DEFAULT, PK/UNIQUE constraints and triggers by
    default, under their original names. Foreign keys are dropped by default, so you don't need
    to seed parent tables.
  - Every part can be toggled: `@keep_defaults`, `@keep_checks`, `@keep_keys`, `@keep_fks`, `@keep_triggers`.
  - It also keeps NOT NULL and IDENTITY by default. `@keep_not_null = 0` makes every column
    nullable (a kept PK stays NOT NULL), `@keep_identity = 0` drops IDENTITY, and
    `@tsqlt_compatible = 1` turns everything off, like `tSQLt.FakeTable`.
- **`EXEC tap.spy_procedure N'dbo.usp_Notify' [, @command]`** replaces the procedure with one
  that logs each call's parameters to `tap.[SpyLog_dbo_usp_Notify]`, then runs `@command`.
  Every parameter defaults to NULL.

## Python tests

The plugin's fixtures:

```python
import pytest

def test_trust_view(mssql_db):                      # the default database, in a rolled-back transaction
    mssql_db.seed("dbo.Trust", [{"TrustCode": "A1", "RegionCode": "N1"}])   # other NOT NULL columns auto-filled
    assert mssql_db.rows("SELECT TrustCode FROM dbo.vw_ActiveTrust") == [{"TrustCode": "A1"}]

@pytest.mark.mssql(database="Reporting", isolation="snapshot", requires=["clr"])
def test_commits(mssql_db): ...

def test_snapshot(mssql_db, snapshot):              # syrupy: pytest --snapshot-update to record
    assert mssql_db.rows("SELECT * FROM dbo.vw_ActiveTrust") == snapshot
```

- **Methods on `mssql_db` (a `Db`):**
  - Queries: `run(sql, params) -> list[ResultSet]`, `rows`, `scalar`, `execute`, `trancount`.
  - Seeding: `seed(table, rows, defaults=)`, `seed_file(path)`.
  - Mocking: `fake_table(...)`, `spy_procedure(...)`, `spy_calls(proc)`.
  - Parameters use `?` placeholders.
- **`mssql_env`** (session scope) holds the container: `connect(name)`, `restore(name)` and `capabilities`.

## Seed data

- **CSV:** one table per file.
  - The table comes from a `-- table: App.Notification` first line, or from the file name
    (`App.Notification.csv`; a bare `Region.csv` means `dbo`).
  - The header row names the columns, and an empty field or `\N` means NULL.
  - `<table>.<N>.csv` (e.g. `Ref.Property.1.csv`, a convention some reference-data exports use): skip `N` header
    rows, then load the values **by position**, in the table's column order. The header text is ignored,
    so it needn't match the column names.
- **YAML or JSON:** `{table: [rows]}`, so several tables fit in one file.
- **Loading behaviour:**
  - Values are `CAST` to each column's type, so `'2026-01-31'`, `'true'` and `'0x0A0B'` all work.
  - Identity values given in the data are kept.
  - **NOT NULL columns with no default that the data leaves out are auto-filled** with a distinct
    placeholder per row. The data only needs the columns the test cares about, and the real
    constraints still apply.
  - Tables loaded together are inserted parents-first, by foreign key.

Seed data can be loaded at three points:

| When | How | Undone by |
|---|---|---|
| Once per database, before the snapshot | `seed = [...]` in config | never: it's the baseline |
| Per SQL test file | `-- tsqltap: seed=...` | the test's transaction |
| Per Python test | `mssql_db.seed(...)` / `seed_file(...)` | the test's transaction |

## Existing tests

- **`*.Tests.sql` scripts** in the older `(Check, Result, Detail)` shape (each script keeps a results table and selects it at the end) run unchanged.
  - Each row is reported as an assertion.
  - Their closing `THROW` (raised when a check fails) is expected, not treated as an error.
  - They default to `isolation=none`, because they manage their own transaction.
- **tSQLt suites (experimental):**
  1. Publish tSQLt (or tSQLt.Edge) and your test classes as one of the configured dacpacs.
  2. Add a file containing `-- tsqltap: tsqlt=all`.
  3. Each row of `tSQLt.TestResult` is reported as an assertion, so tSQLt and `tap` tests appear
     in the same report. That lets you migrate one class at a time.

## Fast local loop

```console
$ uv run pytest --tsqltap-keep     # first run: start + publish (then leave it running)
$ uv run pytest --tsqltap-keep     # later runs: reuse the container; unchanged databases are only reset
$ uv run tsqltap down              # remove it
```

**How reuse works:**
- A database is republished only when one of its inputs changes: the dacpac, scripts, seeds,
  settings or the `tap` library.
- An unchanged database is reused as it is, unless a test committed to it since its last restore.
  Before a test that commits (`isolation=snapshot` or `none`), the database is marked with an
  extended property. The snapshot predates the mark, so restoring removes it. A database still
  marked at the start of a session is restored from its snapshot first, which takes about a second.
- A kept container is named after the project folder and labelled. Only containers that
  tsqltap created this way are ever reused.

**Connections are reused between tests.** Opening one costs about 150 ms through Docker's port
forwarding. Each database keeps one connection, and a test's connection goes back to the pool only
when its changes were rolled back and its session is exactly as it was opened: the same SET options,
database, isolation level, lock timeout, DATEFIRST and language, with no open transaction. Any other
connection is closed, so the next test always starts with a clean session.

`tsqltap up` builds everything and leaves it running, so you can point SSMS or Azure Data
Studio at it. It prints the port.

## CI

Any Linux runner with Docker works: install uv and sqlpackage, then `uv run pytest --junitxml=...`.
Classic SSDT projects build only on Windows, so build the dacpac in an earlier Windows job and pass it
across as an artifact.

- **GitHub Action:** `uses: semics-tech/tsqltap@main` (pin a release tag once one carries
  `action.yml`) runs the suite on a Linux runner. Inputs: `working-directory`, `pytest-args`,
  `junit-file`, `install-sqlpackage`, `tsqltap-version`. Your project must declare tsqltap as a
  dependency (`uv add --dev tsqltap`) and be checked out first.
- **GitHub Actions workflow:** [`examples/github-actions.yml`](https://github.com/semics-tech/tsqltap/blob/main/examples/github-actions.yml).
- **Azure Pipelines:** [`examples/azure-pipelines/tsqltap-job.yml`](https://github.com/semics-tech/tsqltap/blob/main/examples/azure-pipelines/tsqltap-job.yml)
  is a reusable job template. Each assertion appears in the run's Tests tab.

## Limits

- **Engine:** the container runs the SQL Server box engine. Azure SQL Database-only behaviour isn't
  covered; mark such tests `requires=azure`, which is never satisfied today.
- **Azure SQL Database:** the `tap` library and tsqltap's own tests run only on the SQL Server box
  engine (container). Running `tap` directly in Azure SQL Database or Managed Instance is untested,
  so treat it as unverified. Dacpacs from Azure SQL projects do publish to the container.
- **Capabilities:** the container provides `sqlserver`, `linux`, `container` and `snapshot`. Declare
  anything else (e.g. `agent` after enabling SQL Agent) under `capabilities`.
- **Result comparisons:**
  - They use `INSERT ... EXEC`, which can't nest. `@have` can't be a procedure that itself uses
    `INSERT ... EXEC`, or one whose first result set SQL Server can't describe up front (e.g. it
    comes from a temp table the procedure creates).
- **`fake_table`:**
  - It turns computed columns into plain columns.
  - It can't rename a table that a `SCHEMABINDING` view references.

[testcontainers]: https://testcontainers-python.readthedocs.io/
[mssql-python]: https://github.com/microsoft/mssql-python

## From the same team

[Remote SQL Agent](https://github.com/semics-tech/remote-sql-agent) monitors and manages SQL Server Agent jobs across all your servers from one dashboard, with outbound-only connections and no inbound firewall ports.

## Licence

MIT. See [LICENSE](https://github.com/semics-tech/tsqltap/blob/main/LICENSE).
