"""Load test data from CSV / YAML / JSON files, or from Python dicts.

CSV: one file per table. The table is named by a first line `-- table: App.Notification`, or else by
the file name (`App.Notification.csv`; a bare `Notification.csv` means dbo). The header row names the
columns. An empty field or `\\N` is NULL. Named `<table>.<N>.csv`, the
first N rows are skipped and values are positional, in the table's column order.

YAML / JSON: several tables in one file, inserted in file order:

    App.Region:
      - {RegionCode: N1, Name: North}
    App.Trust:
      - {TrustCode: T1, RegionCode: N1}

Every row only needs the columns the test cares about: NOT NULL columns with no default that a row
leaves out are filled with a placeholder of the right type (distinct per row, so unique keys hold).
Values are CAST to the column's type in SQL, so strings like '2026-01-31' or 'true' work anywhere.
Tables loaded together are inserted parents-first according to their foreign keys.
"""

from __future__ import annotations

import csv
import datetime as dt
import glob
import itertools
import json
import uuid
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from tsqltap.connection import BatchError, Db

NULL_MARKERS = ("", "\\N")
_MAX_PARAMS = 2000  # SQL Server allows 2100 parameters per request


class SeedError(Exception):
    pass


@dataclass(frozen=True)
class Column:
    name: str
    type: str
    max_length: int
    precision: int
    scale: int
    is_nullable: bool
    is_identity: bool
    is_computed: bool
    has_default: bool
    generated_always: bool

    @property
    def sql_type(self) -> str:
        t = self.type
        if t in ("varchar", "char", "varbinary", "binary"):
            return f"{t}({'max' if self.max_length == -1 else self.max_length})"
        if t in ("nvarchar", "nchar"):
            return f"{t}({'max' if self.max_length == -1 else self.max_length // 2})"
        if t in ("decimal", "numeric"):
            return f"{t}({self.precision},{self.scale})"
        if t in ("datetime2", "time", "datetimeoffset"):
            return f"{t}({self.scale})"
        return t

    @property
    def insertable(self) -> bool:
        return not (self.is_computed or self.generated_always or self.type == "timestamp")

    @property
    def needs_value(self) -> bool:
        return self.insertable and not (self.is_nullable or self.is_identity or self.has_default)


def table_columns(db: Db, table: str) -> list[Column]:
    rows = db.rows(
        """
        SELECT c.name, TYPE_NAME(c.system_type_id) AS type, c.max_length, c.precision, c.scale,
               c.is_nullable, c.is_identity, c.is_computed,
               CAST(CASE WHEN c.default_object_id <> 0 THEN 1 ELSE 0 END AS BIT) AS has_default,
               CAST(CASE WHEN c.generated_always_type <> 0 THEN 1 ELSE 0 END AS BIT) AS generated_always
        FROM sys.columns c
        WHERE c.object_id = OBJECT_ID(?, N'U')
        ORDER BY c.column_id
        """,
        table,
    )
    if not rows:
        raise SeedError(f"no table {table} in database {db.database}")
    return [
        Column(
            name=r["name"],
            type=r["type"],
            max_length=int(r["max_length"]),
            precision=int(r["precision"]),
            scale=int(r["scale"]),
            is_nullable=bool(r["is_nullable"]),
            is_identity=bool(r["is_identity"]),
            is_computed=bool(r["is_computed"]),
            has_default=bool(r["has_default"]),
            generated_always=bool(r["generated_always"]),
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------- placeholders

_counters: dict[tuple[str, str], Iterator[int]] = defaultdict(lambda: itertools.count(1))


def placeholder(table: str, col: Column) -> Any:
    """A value of the column's type, different on every call for the same column."""
    n = next(_counters[(table.lower(), col.name.lower())])
    t = col.type
    if t in ("int", "bigint", "decimal", "numeric", "money", "smallmoney", "float", "real", "sql_variant"):
        return n
    if t == "smallint":
        return n % 32767
    if t == "tinyint":
        return n % 256
    if t == "bit":
        return 0
    if t in ("varchar", "char", "nvarchar", "nchar"):
        text = f"{col.name}{n}"
        if col.max_length != -1:
            limit = col.max_length // 2 if t.startswith("n") else col.max_length
            text = text[-limit:]
        return text
    if t in ("date", "datetime", "datetime2", "smalldatetime", "datetimeoffset"):
        return (dt.date(2000, 1, 1) + dt.timedelta(days=n)).isoformat()
    if t == "time":
        return f"{n % 24:02d}:00:00"
    if t == "uniqueidentifier":
        return str(uuid.uuid4())
    if t in ("binary", "varbinary"):
        return n.to_bytes(4, "big")[-max(1, min(4, col.max_length if col.max_length > 0 else 4)) :]
    if t == "xml":
        return "<placeholder/>"
    raise SeedError(f"cannot invent a value for {table}.{col.name} ({t}): give it a value in the seed data")


def _convert(col: Column, value: Any) -> Any:
    if isinstance(value, str) and col.type in ("binary", "varbinary", "image") and value.lower().startswith("0x"):
        return bytes.fromhex(value[2:])
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    return value


# ---------------------------------------------------------------------------- inserting


def insert_rows(
    db: Db,
    table: str,
    rows: list[dict[str, Any]],
    defaults: dict[str, Any] | None = None,
    autofill: bool = True,
) -> int:
    if not rows:
        return 0
    defaults = defaults or {}
    columns = table_columns(db, table)
    by_name = {c.name.lower(): c for c in columns}

    given = {k.lower() for row in rows for k in row} | {k.lower() for k in defaults}
    unknown = sorted(given - by_name.keys())
    if unknown:
        raise SeedError(f"{table} has no column(s): {', '.join(unknown)}")

    chosen = [c for c in columns if c.name.lower() in given or (autofill and c.needs_value)]
    for c in chosen:
        if not c.insertable:
            raise SeedError(f"{table}.{c.name} is computed / generated and cannot be seeded")

    def value(row: dict[str, Any], col: Column) -> Any:
        for source in (row, defaults):
            for k, v in source.items():
                if k.lower() == col.name.lower():
                    return _convert(col, v)
        if autofill and col.needs_value:
            return placeholder(table, col)
        return None

    values = [[value(row, c) for c in chosen] for row in rows]
    identity = any(c.is_identity for c in chosen)

    target = _quote_table(table)
    col_list = ", ".join(f"[{c.name}]" for c in chosen)
    row_sql = "(" + ", ".join(f"CAST(? AS {c.sql_type})" for c in chosen) + ")"
    per_chunk = max(1, min(1000, _MAX_PARAMS // max(1, len(chosen))))

    if identity:
        db.execute(f"SET IDENTITY_INSERT {target} ON")
    try:
        for start in range(0, len(values), per_chunk):
            chunk = values[start : start + per_chunk]
            sql = f"INSERT INTO {target} ({col_list}) VALUES " + ", ".join([row_sql] * len(chunk))
            try:
                db.run(sql, [v for row in chunk for v in row])
            except BatchError as exc:
                raise BatchError(f"seeding {table}: {exc.message}", exc.results) from exc
    finally:
        if identity:
            db.execute(f"SET IDENTITY_INSERT {target} OFF")
    return len(values)


def _quote_table(table: str) -> str:
    parts = [p.strip("[]") for p in table.split(".")]
    return ".".join(f"[{p}]" for p in parts)


# ---------------------------------------------------------------------------- files


def read_csv(path: Path) -> tuple[str, list[dict[str, Any]]]:
    text = path.read_text(encoding="utf-8-sig")
    lines = text.splitlines(keepends=True)
    table = None
    if lines and lines[0].lstrip().startswith("--"):
        head = lines[0].strip().lstrip("-").strip()
        if head.lower().startswith("table:"):
            table = head.split(":", 1)[1].strip()
            lines = lines[1:]
    skip_rows: int | None = None
    if table is None:
        stem = path.name[: -len(".csv")]
        # The `<table>.<N>.csv` convention (used by some reference-data exports): skip N header rows, then the
        # values are positional, in the table's column order -- the header text need not match the
        # column names, and in that data often does not ("Short Description" for ShortDescription).
        head, _, flag = stem.rpartition(".")
        if head and flag.isdigit():
            stem, skip_rows = head, int(flag)
        table = stem if "." in stem else f"dbo.{stem}"
    if skip_rows is not None:
        rows: list[dict[Any, Any]] = [
            {i: (None if v in NULL_MARKERS else v) for i, v in enumerate(r)}
            for r in list(csv.reader(lines))[skip_rows:]
            if r
        ]
    else:
        rows = [{k: (None if v in NULL_MARKERS else v) for k, v in r.items()} for r in csv.DictReader(lines)]
    return table, rows


def _positional(db: Db, table: str, rows: list[dict[Any, Any]]) -> list[dict[str, Any]]:
    """Name the values of a headerless CSV by the table's insertable, non-identity columns, in order."""
    if not rows or not isinstance(next(iter(rows[0]), None), int):
        return rows
    columns = [c.name for c in table_columns(db, table) if c.insertable and not c.is_identity]
    width = max(len(r) for r in rows)
    if width > len(columns):
        raise SeedError(f"{table}: headerless CSV has {width} values per row but only {len(columns)} columns to fill")
    return [{columns[i]: v for i, v in r.items()} for r in rows]


def read_structured(path: Path) -> list[tuple[str, list[dict[str, Any]]]]:
    text = path.read_text(encoding="utf-8-sig")
    data = json.loads(text) if path.suffix.lower() == ".json" else yaml.safe_load(text)
    if data is None:
        return []
    if not isinstance(data, dict):
        raise SeedError(f"{path}: expected a mapping of table name to a list of rows")
    out = []
    for table, rows in data.items():
        if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
            raise SeedError(f"{path}: {table} must be a list of rows (mappings)")
        out.append((str(table), rows))
    return out


def read_file(path: Path) -> list[tuple[str, list[dict[str, Any]]]]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return [read_csv(path)]
    if suffix in (".yaml", ".yml", ".json"):
        return read_structured(path)
    raise SeedError(f"unsupported seed file type: {path}")


def fk_order(db: Db, tables: list[str]) -> list[str]:
    """Stable topological sort: parents before children, otherwise the order given."""
    ids = {t: db.scalar("SELECT OBJECT_ID(?, N'U')", t) for t in tables}
    missing = [t for t, i in ids.items() if i is None]
    if missing:
        raise SeedError(f"no such table(s): {', '.join(missing)}")
    edges = db.rows("SELECT parent_object_id AS child, referenced_object_id AS parent FROM sys.foreign_keys")
    by_id = {i: t for t, i in ids.items()}
    parents: dict[str, set[str]] = {t: set() for t in tables}
    for e in edges:
        child, parent = by_id.get(e["child"]), by_id.get(e["parent"])
        if child and parent and child != parent:
            parents[child].add(parent)
    ordered: list[str] = []
    remaining = list(tables)
    while remaining:
        ready = next((t for t in remaining if parents[t] <= set(ordered)), remaining[0])  # cycles: keep order
        ordered.append(ready)
        remaining.remove(ready)
    return ordered


def load_files(db: Db, paths: list[Path]) -> int:
    datasets: dict[str, list[dict[str, Any]]] = {}
    for path in paths:
        for table, rows in read_file(path):
            datasets.setdefault(table, []).extend(_positional(db, table, rows))
    total = 0
    for table in fk_order(db, list(datasets)):
        total += insert_rows(db, table, datasets[table])
    return total


def resolve_seed_paths(specs: list[str], base: Path, root: Path | None = None) -> list[Path]:
    """Seed paths/globs relative to the test file, falling back to the project root."""
    found: list[Path] = []
    for spec in specs:
        for directory in (base, root):
            if directory is None:
                continue
            matches = sorted(glob.glob(str(directory / spec), recursive=True))
            if matches:
                found.extend(Path(m) for m in matches)
                break
        else:
            raise SeedError(f"seed file not found: {spec}")
    return found
