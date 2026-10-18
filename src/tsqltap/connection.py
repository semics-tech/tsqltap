"""A thin wrapper over mssql-python: run a batch, get back every result set it produced."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

import mssql_python

# Pooled connections would hold the database open and block RESTORE ... FROM DATABASE_SNAPSHOT,
# which needs exclusive access.
mssql_python.pooling(enabled=False)

_SERVER_MSG = re.compile(r"\[SQL Server\](.*)$", re.DOTALL)


@dataclass
class ResultSet:
    columns: list[str]
    rows: list[dict[str, Any]]

    def has_columns(self, *names: str) -> bool:
        lowered = {c.lower() for c in self.columns}
        return all(n.lower() in lowered for n in names)


@dataclass
class BatchError(Exception):
    """A batch raised after (possibly) returning some result sets."""

    message: str
    results: list[ResultSet] = field(default_factory=list)

    def __str__(self) -> str:
        return self.message


def server_message(exc: BaseException) -> str:
    """The SQL Server error text, without the driver's prefix."""
    text = str(exc)
    m = _SERVER_MSG.search(text)
    return m.group(1).strip() if m else text


@dataclass(frozen=True)
class ServerInfo:
    host: str
    port: int
    user: str
    password: str

    def connection_string(self, database: str = "master") -> str:
        return (
            f"Server={self.host},{self.port};Database={database};UID={self.user};PWD={self.password};"
            "Encrypt=no;TrustServerCertificate=yes"
        )


class Db:
    """One connection to one database. Autocommit is on: transactions are explicit T-SQL."""

    def __init__(self, server: ServerInfo, database: str = "master") -> None:
        self.server = server
        self.database = database
        self._conn = mssql_python.connect(server.connection_string(database), autocommit=True)
        self._cur = self._conn.cursor()

    def close(self) -> None:
        try:
            self._cur.close()
        finally:
            self._conn.close()

    def __enter__(self) -> Db:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------ execution

    def run(self, sql: str, params: Sequence[Any] = ()) -> list[ResultSet]:
        """Execute one batch and return every result set it produced.

        If the batch raises, the result sets already received are attached to the BatchError:
        the server sends them before the error, and the driver raises only on the nextset()
        call that reaches it.
        """
        results: list[ResultSet] = []
        try:
            self._cur.execute(sql, tuple(params)) if params else self._cur.execute(sql)
            while True:
                if self._cur.description is not None:
                    columns = [d[0] for d in self._cur.description]
                    rows = [dict(zip(columns, row, strict=True)) for row in self._cur.fetchall()]
                    results.append(ResultSet(columns, rows))
                if not self._cur.nextset():
                    break
        except mssql_python.Error as exc:
            raise BatchError(server_message(exc), results) from exc
        return results

    def execute(self, sql: str, *params: Any) -> None:
        self.run(sql, params)

    def executemany(self, sql: str, rows: Iterable[Sequence[Any]]) -> None:
        try:
            self._cur.executemany(sql, [tuple(r) for r in rows])
        except mssql_python.Error as exc:
            raise BatchError(server_message(exc)) from exc

    def rows(self, sql: str, *params: Any) -> list[dict[str, Any]]:
        """Rows of the first result set (empty list if there is none)."""
        results = self.run(sql, params)
        return results[0].rows if results else []

    def scalar(self, sql: str, *params: Any) -> Any:
        rows = self.rows(sql, *params)
        if not rows:
            return None
        return next(iter(rows[0].values()))

    @property
    def trancount(self) -> int:
        return int(self.scalar("SELECT @@TRANCOUNT"))

    # ------------------------------------------------------------------ helpers for Python tests

    def seed(
        self,
        table: str,
        rows: Iterable[dict[str, Any]],
        defaults: dict[str, Any] | None = None,
        autofill: bool = True,
    ) -> int:
        """Insert rows into a table, filling required columns the rows leave out. See seed.py."""
        from tsqltap.seed import insert_rows

        return insert_rows(self, table, list(rows), defaults=defaults, autofill=autofill)

    def seed_file(self, path: str) -> int:
        from pathlib import Path

        from tsqltap.seed import load_files

        return load_files(self, [Path(path)])

    def fake_table(
        self,
        table: str,
        *,
        keep_defaults: bool = True,
        keep_checks: bool = True,
        keep_keys: bool = True,
        keep_fks: bool = False,
        keep_triggers: bool = True,
    ) -> None:
        """Swap `table` for an empty copy (undone by the test's rollback). See tap.fake_table."""
        self.execute(
            "EXEC tap.fake_table @table = ?, @keep_defaults = ?, @keep_checks = ?, @keep_keys = ?,"
            " @keep_fks = ?, @keep_triggers = ?",
            table,
            keep_defaults,
            keep_checks,
            keep_keys,
            keep_fks,
            keep_triggers,
        )

    def spy_procedure(self, procedure: str, command: str | None = None) -> None:
        """Replace a procedure with a spy that logs each call's parameters, then runs `command`."""
        self.execute("EXEC tap.spy_procedure @procedure = ?, @command = ?", procedure, command)

    def spy_calls(self, procedure: str) -> list[dict[str, Any]]:
        log = self.scalar("SELECT tap.spy_log_name(?)", procedure)
        return self.rows(f"SELECT * FROM {log} ORDER BY _id_")
