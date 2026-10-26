# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/). Until 1.0, minor versions may change behaviour.

## [Unreleased]

- A stalled `sqlpackage` publish no longer hangs the session: stdin is detached and a `publish_timeout` (seconds, default 1800; env `TSQLTAP_PUBLISH_TIMEOUT`) raises a `ProvisionError` naming the database with the last output.
- Warns once per session when `sqlpackage` is older than 170 (162.x has been seen to hang), pointing at `TSQLTAP_SQLPACKAGE`.
- A file under `isolation=transaction` that leaves its transaction uncommittable (`XACT_STATE() = -1`) now fails with a message recommending `-- tsqltap: isolation=snapshot` and listing the usual causes.

## [0.1.1]

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
