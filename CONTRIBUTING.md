# Contributing

Issues and pull requests are welcome. For anything larger than a small fix, please open an issue
first, so we can agree the approach before you spend time on it.

## Development setup

You need Python 3.12 or later, [uv](https://docs.astral.sh/uv/) and Docker.

```console
$ uv sync
$ uv run pytest                    # starts a SQL Server container; about a minute
$ uv run pytest --tsqltap-keep     # keep the container between runs for a faster loop
$ uv run tsqltap down              # remove it
```

The self-tests build their databases from SQL scripts in `tests/fixture_db`, so they need
neither sqlpackage nor a dacpac.

## Before you open a pull request

```console
$ uv run ruff format src tests
$ uv run ruff check src tests
$ uv run mypy src
$ uv run pytest
```

- Add a test for the change. A `tap` assertion needs a test for both its passing and its failing
  output (see `tests/test_failures.py`).
- Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/):
  `feat:`, `fix:`, `perf:`, `docs:`, `test:` and so on.
- Add a line under **Unreleased** in `CHANGELOG.md` for any change a user would notice.
