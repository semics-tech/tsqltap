# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/). Until 1.0, minor versions may change behaviour.

## [Unreleased]

- Fails fast, with advice, when Docker is not reachable or SQL Server does not become ready: a `startup_timeout` (seconds, default 900, enough for a cold image pull; env `TSQLTAP_STARTUP_TIMEOUT`) raises a `ProvisionError` with the container's last log lines.
- Test folders named `build` (or matching any other `norecursedirs` pattern) under a database's `tests` path are now collected instead of silently skipped, including below `**` globs; this replaces the usage error for a `tests` path itself named `build`.
- Fix: with `--tsqltap-keep`, a database is now rebuilt when any database listed above it changes, not only when its own inputs do (previously a republished `A` left `B` reused from a snapshot built against the old `A`). The first kept session after upgrading rebuilds every database after the first.- SQLCMD variables are read from the dacpac's `model.xml`: their `DefaultValue`s are used wherever `variables` sets nothing, and a variable with no value anywhere fails before publishing with its name (previously it published as an empty string). Publish profiles are not yet supported ([#15](https://github.com/semics-tech/tsqltap/issues/15)).

## [0.1.1]

- The pytest summary ends with a line such as `tsqltap: 3 files, 41 assertions (38 passed, 1 failed, 2 todo)`, so the assertion count shows without `-v`.
- `tap.fake_table` takes `@keep_not_null`, `@keep_identity` and `@tsqlt_compatible` (defaults unchanged), so a test can insert only the columns it cares about, as with `tSQLt.FakeTable`.
- A stalled `sqlpackage` publish no longer hangs the session: stdin is detached and a `publish_timeout` (seconds, default 1800; env `TSQLTAP_PUBLISH_TIMEOUT`) raises a `ProvisionError` naming the database with the last output.
- Warns once per session when `sqlpackage` is older than 170 (162.x has been seen to hang), pointing at `TSQLTAP_SQLPACKAGE`.
- A file under `isolation=transaction` that leaves its transaction uncommittable (`XACT_STATE() = -1`) now fails with a message recommending `-- tsqltap: isolation=snapshot` and listing the usual causes.
- `tsqltap script -o tap.sql` writes the `tap` library as one standalone `tap.sql`, for SSMS or `sqlcmd` use.
- Each GitHub release now has `tap.sql` attached as a release asset.
- Docs: "Use tap from SSMS / sqlcmd without Python".
- Docs: Azure SQL Database wording, noting it is untested.
- A composite GitHub Action (`action.yml`) that runs the suite in CI.
- Added SECURITY.md with a private vulnerability reporting route.
- Docs: quickstart, comparison with tSQLt and pgTAP, and absolute links so the PyPI page renders.

## [0.1.0]

First release.

- `tap` assertion library in plain T-SQL, installed only into test databases:
  - value checks, result-set comparisons (`results_eq`, `bag_eq`, `set_eq`), `throws_ok` / `lives_ok`;
  - schema and permission checks;
  - TODO blocks for known failures;
  - `fake_table` and `spy_procedure` mocks.
- pytest plugin: `*.test.sql` files are tests and each assertion is a subtest (its own JUnit
  `<testcase>`). Includes `mssql_db` / `mssql_env` fixtures for Python tests.
- A disposable SQL Server 2025 container via testcontainers, with dacpac publish via sqlpackage,
  server-level setup scripts, baseline seeds and finalize scripts. Each database is snapshotted
  after it is built.
- Isolation per test: `transaction` (default), `snapshot` and `none`.
- Seeding from CSV, YAML, JSON or Python dicts, with required columns auto-filled and FK ordering.
- `--tsqltap-keep`: reuse the container and any unchanged database between runs.
- Connections are pooled between tests whose session state is unchanged.
- Older `(Check, Result, Detail)` scripts run unchanged; tSQLt suites run through an experimental bridge.
- `tsqltap up | down | new` CLI.
