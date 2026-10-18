"""Reads `[tool.tsqltap]` from the project's pyproject.toml.

Example::

    [tool.tsqltap]
    image = "mcr.microsoft.com/mssql/server:2025-CU9-ubuntu-24.04"
    test_paths = ["tests/sql"]

    [[tool.tsqltap.databases]]          # published in list order
    name = "Shop"
    dacpac = "src/Shop/bin/Release/Shop.dacpac"
    scripts = ["tests/setup/*.sql"]      # run after the dacpac, in sorted order
    seed = ["tests/seed/baseline/*.csv"]
    tests = ["src/Shop/Tests/**"]   # test files here default to this database
    variables = { Environment = "test", OtherDb = "{db:Reporting}" }
    publish = { ExcludeObjectTypes = "Users;Logins;RoleMembership" }
"""

from __future__ import annotations

import fnmatch
import glob
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_IMAGE = "mcr.microsoft.com/mssql/server:2025-CU9-ubuntu-24.04"
DEFAULT_SQL_GLOBS = ("*.test.sql", "*.Tests.sql")


class ConfigError(Exception):
    pass


@dataclass
class DatabaseConfig:
    name: str
    dacpac: Path | None = None
    scripts: list[str] = field(default_factory=list)
    seed: list[str] = field(default_factory=list)
    variables: dict[str, str] = field(default_factory=dict)
    publish: dict[str, Any] = field(default_factory=dict)
    install_tap: bool = True
    # Test files under these paths (globs relative to the project root) default to this database.
    tests: list[str] = field(default_factory=list)
    # Scripts run after the baseline seed and before the snapshot, e.g. procs that merge staged seed
    # rows into the tables the code under test reads.
    finalize: list[str] = field(default_factory=list)
    # Query Store is on by default from SQL Server 2022, and its background flush makes every
    # snapshot restore wait ~3 s for SINGLE_USER WITH ROLLBACK IMMEDIATE. Off unless a test needs it.
    query_store: bool = False

    def script_files(self, root: Path) -> list[Path]:
        return _expand(root, self.scripts)

    def seed_files(self, root: Path) -> list[Path]:
        return _expand(root, self.seed)

    def finalize_files(self, root: Path) -> list[Path]:
        return _expand(root, self.finalize)


@dataclass
class Config:
    root: Path
    image: str = DEFAULT_IMAGE
    edition: str = "Developer"
    # Server (and so tempdb) collation. Set it to your production databases' collation when code
    # under test compares temp-table columns with database columns.
    collation: str | None = None
    # sa password. Unset: a random one per session, or KEPT_PASSWORD for a kept container (which a
    # later session has to be able to log in to).
    password: str | None = None
    sql_globs: tuple[str, ...] = DEFAULT_SQL_GLOBS
    databases: list[DatabaseConfig] = field(default_factory=list)
    sqlpackage: str = "sqlpackage"
    # Extra capabilities tests may `require` (e.g. "agent" if you enable SQL Agent in the image).
    capabilities: list[str] = field(default_factory=list)
    # Server-level scripts (globs, run in master once the container is up, before any database is
    # built): logins, linked servers, server options. They run on every session, so make them re-runnable.
    setup: list[str] = field(default_factory=list)

    def setup_files(self) -> list[Path]:
        return _expand(self.root, self.setup)

    @property
    def default_database(self) -> DatabaseConfig:
        if not self.databases:
            raise ConfigError("[tool.tsqltap] defines no databases")
        return self.databases[0]

    def database(self, name: str | None) -> DatabaseConfig:
        if name is None:
            return self.default_database
        for db in self.databases:
            if db.name.lower() == name.lower():
                return db
        raise ConfigError(f"no database named {name!r} in [tool.tsqltap].databases")

    def database_for(self, path: Path, explicit: str | None = None) -> DatabaseConfig:
        """The database a test file runs in: the one it names, else the one whose `tests` globs
        match its path most specifically (longest pattern), else the first configured."""
        if explicit:
            return self.database(explicit)
        try:
            rel = path.resolve().relative_to(self.root.resolve()).as_posix()
        except ValueError:
            return self.default_database
        best: tuple[int, DatabaseConfig] | None = None
        for db in self.databases:
            for pattern in db.tests:
                pattern = pattern.replace("\\", "/").rstrip("/")
                matches = fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(rel, pattern + "/*")
                if matches and (best is None or len(pattern) > best[0]):
                    best = (len(pattern), db)
        return best[1] if best else self.default_database


def _expand(root: Path, patterns: list[str]) -> list[Path]:
    files: list[Path] = []
    for pattern in patterns:
        matches = sorted(glob.glob(str(root / pattern), recursive=True))
        if not matches and not glob.has_magic(pattern):
            raise ConfigError(f"file not found: {pattern}")
        files.extend(Path(m) for m in matches)
    return files


def load(root: Path) -> Config:
    """Load config from `root/pyproject.toml`. Env vars override the container settings."""
    pyproject = root / "pyproject.toml"
    raw: dict[str, Any] = {}
    if pyproject.exists():
        with pyproject.open("rb") as f:
            raw = tomllib.load(f).get("tool", {}).get("tsqltap", {})

    databases = []
    for entry in raw.get("databases", []):
        if "name" not in entry:
            raise ConfigError("every [[tool.tsqltap.databases]] entry needs a name")
        dacpac = entry.get("dacpac")
        databases.append(
            DatabaseConfig(
                name=entry["name"],
                dacpac=(root / dacpac) if dacpac else None,
                scripts=list(entry.get("scripts", [])),
                seed=list(entry.get("seed", [])),
                variables={k: str(v) for k, v in entry.get("variables", {}).items()},
                publish=dict(entry.get("publish", {})),
                install_tap=bool(entry.get("install_tap", True)),
                tests=[str(t) for t in entry.get("tests", [])],
                finalize=[str(t) for t in entry.get("finalize", [])],
                query_store=bool(entry.get("query_store", False)),
            )
        )

    return Config(
        root=root,
        image=os.environ.get("TSQLTAP_IMAGE", raw.get("image", DEFAULT_IMAGE)),
        edition=os.environ.get("TSQLTAP_EDITION", raw.get("edition", "Developer")),
        collation=os.environ.get("TSQLTAP_COLLATION", raw.get("collation")),
        password=os.environ.get("TSQLTAP_PASSWORD", raw.get("password")),
        sql_globs=tuple(raw.get("sql_globs", DEFAULT_SQL_GLOBS)),
        databases=databases,
        sqlpackage=os.environ.get("TSQLTAP_SQLPACKAGE", raw.get("sqlpackage", "sqlpackage")),
        capabilities=[str(c) for c in raw.get("capabilities", [])],
        setup=[str(x) for x in raw.get("setup", [])],
    )
