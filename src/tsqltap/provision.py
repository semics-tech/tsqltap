"""Start SQL Server in a container (testcontainers) and build the configured databases in it.

For each database, in config order:
  1. publish the dacpac with sqlpackage (or CREATE DATABASE if there is none);
  2. run the setup scripts;
  3. install the `tap` assertion library;
  4. load the baseline seed data, then run the finalize scripts;
  5. take a database snapshot, so tests that commit can be undone with RESTORE ... FROM DATABASE_SNAPSHOT.

With `keep=True` the container outlives the test session and is picked up again next time; a
database whose inputs (dacpac, scripts, seeds, tap, settings) are unchanged is then reset from its
snapshot instead of being republished, which turns a minutes-long publish into a second or two.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import ntpath
import posixpath
import re
import secrets
import subprocess
import time
from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING

from tsqltap.config import Config, DatabaseConfig
from tsqltap.connection import BatchError, Db, ServerInfo
from tsqltap.seed import SeedError, load_files
from tsqltap.sqlfile import split_batches

if TYPE_CHECKING:
    from testcontainers.community.mssql import SqlServerContainer

log = logging.getLogger("tsqltap")

LABEL_PROJECT = "tsqltap.project"
LABEL_IMAGE = "tsqltap.image"
LABEL_SETTINGS = "tsqltap.settings"
FINGERPRINT_PROPERTY = "tsqltap.fingerprint"
# Set on a database before a test that commits; the snapshot predates it, so a restore removes it.
DIRTY_PROPERTY = "tsqltap.dirty"
# Session state a pooled connection must still have when it is handed back (see Environment.release).
_SESSION_STATE = """
SELECT @@OPTIONS AS options, DB_NAME() AS db, @@TRANCOUNT AS trancount, @@LOCK_TIMEOUT AS lock_timeout,
       @@DATEFIRST AS datefirst, @@LANGUAGE AS language,
       (SELECT transaction_isolation_level FROM sys.dm_exec_sessions WHERE session_id = @@SPID) AS isolation,
       CONVERT(NVARCHAR(4000), SESSION_CONTEXT(N'tap.todo')) AS todo
"""
SNAPSHOT_SUFFIX = "__tsqltap_snap"
# The sa password of a kept container when none is configured. It is well known, so a kept
# container's port is bound to 127.0.0.1 only.
KEPT_PASSWORD = "Tsqltap!Passw0rd"
DEFAULT_PUBLISH = {
    "CreateNewDatabase": True,
    "BlockOnPossibleDataLoss": False,
    # Projects targeting Azure SQL Database still publish to the box engine in the container.
    "AllowIncompatiblePlatform": True,
}
# The container always has these; anything else a test `requires` must be declared in config.
BASE_CAPABILITIES = frozenset({"sqlserver", "linux", "container", "snapshot"})


MIN_SQLPACKAGE_MAJOR = 170


class ProvisionError(Exception):
    pass


def tap_scripts() -> list[tuple[str, str]]:
    folder = resources.files("tsqltap") / "sql" / "tap"
    return sorted((f.name, f.read_text(encoding="utf-8")) for f in folder.iterdir() if f.name.endswith(".sql"))


def run_script(db: Db, text: str, source: str) -> None:
    for batch in split_batches(text):
        try:
            db.run(batch)
        except BatchError as exc:
            raise ProvisionError(f"{source}: {exc.message}") from exc


def container_name(root: Path) -> str:
    return "tsqltap-" + hashlib.sha1(str(root.resolve()).lower().encode()).hexdigest()[:10]


class Environment:
    """One SQL Server container plus the databases built in it."""

    def __init__(self, cfg: Config, keep: bool = False) -> None:
        self.cfg = cfg
        self.keep = keep
        self.server: ServerInfo | None = None
        self.capabilities: set[str] = set(BASE_CAPABILITIES)
        # A prefix covering each class SQL Server's password policy requires, then hex. Never a leading
        # "-": testcontainers' readiness check passes it to `sqlcmd -P`, which would read an option.
        self.password = cfg.password or (KEPT_PASSWORD if keep else "Mt1!" + secrets.token_hex(16))
        self._container: SqlServerContainer | None = None
        self._snapshots: dict[str, bool] = {}
        # Connections are slow to open (~150 ms through Docker's port forwarding) and tests are many, so
        # each database keeps one connection between tests: see lease() / release().
        self._pool: dict[str, Db] = {}
        self._baseline: dict[int, tuple[object, ...] | None] = {}
        self._master: Db | None = None
        self._sqlpackage_checked = False

    # ------------------------------------------------------------------ container

    def start(self) -> ServerInfo:
        if self.keep:
            self.server = self._attach_kept()
        if self.server is None:
            self.server = self._start_container()
        return self.server

    def _start_container(self) -> ServerInfo:
        from testcontainers.community.mssql import SqlServerContainer
        from testcontainers.core.config import testcontainers_config

        name = container_name(self.cfg.root)
        container = SqlServerContainer(self.cfg.image, password=self.password)
        container.with_env("MSSQL_PID", self.cfg.edition)
        if self.cfg.collation:
            container.with_env("MSSQL_COLLATION", self.cfg.collation)
        if self.keep:
            # Ryuk removes every container from this session when the process exits: a kept
            # container has to opt out of it, and is removed by `tsqltap down` instead.
            testcontainers_config.ryuk_disabled = True
            self._remove_named(name)
            container.with_name(name)
            # Loopback only (any free port). with_bind_ports can't express an address, but the map is
            # handed to docker-py, which takes (ip, port).
            container.ports["1433"] = ("127.0.0.1", None)  # type: ignore[assignment]
            container.with_kwargs(
                labels={
                    LABEL_PROJECT: str(self.cfg.root),
                    LABEL_IMAGE: self.cfg.image,
                    LABEL_SETTINGS: self._settings(),
                }
            )
        started = time.perf_counter()
        log.info("starting %s", self.cfg.image)
        container.start()
        log.info("container ready in %.1fs", time.perf_counter() - started)
        self._container = container
        return ServerInfo(
            # The kept container listens on IPv4 loopback only; "localhost" may resolve to ::1 first.
            host="127.0.0.1" if self.keep else container.get_container_host_ip(),
            port=int(container.get_exposed_port(1433)),
            user="sa",
            password=self.password,
        )

    def _attach_kept(self) -> ServerInfo | None:
        import docker

        client = docker.from_env()
        try:
            found = client.containers.get(container_name(self.cfg.root))
        except docker.errors.NotFound:
            return None
        if (
            found.status != "running"
            or found.labels.get(LABEL_IMAGE) != self.cfg.image
            or found.labels.get(LABEL_SETTINGS) != self._settings()
        ):
            return None
        ports = found.attrs["NetworkSettings"]["Ports"].get("1433/tcp") or []
        if not ports:
            return None
        server = ServerInfo("127.0.0.1", int(ports[0]["HostPort"]), "sa", self.password)
        try:
            Db(server).close()
        except Exception:  # any failure to connect means: start a fresh container instead
            return None
        log.info("reusing kept container %s", found.name)
        return server

    def _settings(self) -> str:
        """What a kept container must match to be reused (besides the image)."""
        return f"{self.cfg.edition}|{self.cfg.collation or ''}"

    @staticmethod
    def _remove_named(name: str) -> None:
        import docker

        with contextlib.suppress(docker.errors.NotFound):
            docker.from_env().containers.get(name).remove(force=True)

    def stop(self) -> None:
        self._close_pool()
        if self._master is not None:
            self._master.close()
            self._master = None
        if self._container is not None and not self.keep:
            self._container.stop()
        self._container = None

    # ------------------------------------------------------------------ databases

    def connect(self, database: str = "master") -> Db:
        """A new connection of its own, which the caller closes."""
        if self.server is None:
            raise ProvisionError("environment not started")
        return Db(self.server, database)

    # ------------------------------------------------------------------ the connection pool

    def lease(self, database: str) -> Db:
        """A connection to `database` for one test: the pooled one if there is one, else a new one."""
        db = self._pool.pop(database.lower(), None)
        if db is None:
            db = self.connect(database)
            self._baseline[id(db)] = self._session_state(db)
        return db

    def release(self, db: Db, reusable: bool) -> None:
        """Hand a leased connection back. It is pooled only if the caller says it is clean (the test ran
        inside a transaction that was rolled back) AND its session state is exactly as it was opened --
        otherwise a SET option, USE, open transaction or TODO marker could leak into the next test."""
        baseline = self._baseline.pop(id(db), None)
        key = db.database.lower()
        if reusable and baseline is not None and key not in self._pool and self._session_state(db) == baseline:
            self._baseline[id(db)] = baseline
            self._pool[key] = db
        else:
            db.close()

    @staticmethod
    def _session_state(db: Db) -> tuple[object, ...] | None:
        try:
            rows = db.rows(_SESSION_STATE)
        except BatchError:
            return None
        return tuple(rows[0].values()) if rows else None  # no row: e.g. SET NOEXEC ON

    def _drop_pooled(self, database: str) -> None:
        db = self._pool.pop(database.lower(), None)
        if db is not None:
            self._baseline.pop(id(db), None)
            db.close()

    def _close_pool(self) -> None:
        for name in list(self._pool):
            self._drop_pooled(name)

    def master(self) -> Db:
        """The environment's own master connection, kept for restores and markers."""
        if self._master is None:
            self._master = self.connect()
        return self._master

    # ------------------------------------------------------------------ dirty markers

    def mark_dirty(self, *databases: str) -> None:
        """Record that a test is about to commit in these databases, so a later session sharing the
        container (--tsqltap-keep) knows to restore them. The snapshot predates the marker, so a
        restore removes it."""
        for name in databases:
            name = self.cfg.database(name).name
            self.master().execute(
                f"IF NOT EXISTS (SELECT 1 FROM [{name}].sys.extended_properties WHERE class = 0 AND name = ?) "
                f"EXEC [{name}].sys.sp_addextendedproperty @name = ?, @value = 1",
                DIRTY_PROPERTY,
                DIRTY_PROPERTY,
            )

    @staticmethod
    def _is_dirty(master: Db, name: str) -> bool:
        return bool(
            master.scalar(
                f"SELECT COUNT(*) FROM [{name}].sys.extended_properties WHERE class = 0 AND name = ?",
                DIRTY_PROPERTY,
            )
        )

    def provision(self) -> None:
        master = self.master()
        for script in self.cfg.setup_files():
            run_script(master, script.read_text(encoding="utf-8-sig"), str(script))
        for dbcfg in self.cfg.databases:
            self._provision_one(master, dbcfg)
        self.capabilities |= {c.lower() for c in self.cfg.capabilities}

    def fingerprint(self, dbcfg: DatabaseConfig) -> str:
        h = hashlib.sha256()
        h.update(
            json.dumps(
                [self.cfg.image, dbcfg.variables, dbcfg.publish, dbcfg.install_tap, dbcfg.query_store],
                sort_keys=True,
                default=str,
            ).encode()
        )
        files = [
            *self.cfg.setup_files(),  # a database built against other server-level setup is a different build
            *([dbcfg.dacpac] if dbcfg.dacpac else []),
            *dbcfg.script_files(self.cfg.root),
            *dbcfg.seed_files(self.cfg.root),
            *dbcfg.finalize_files(self.cfg.root),
        ]
        for f in files:
            h.update(str(f).encode())
            h.update(Path(f).read_bytes())
        for name, text in tap_scripts():
            h.update(name.encode())
            h.update(text.encode())
        return h.hexdigest()

    def _provision_one(self, master: Db, dbcfg: DatabaseConfig) -> None:
        name = dbcfg.name
        fp = self.fingerprint(dbcfg)
        if self.keep and self._current_fingerprint(master, name) == fp and self._has_snapshot(master, name):
            self._snapshots[name] = True
            if self._is_dirty(master, name):
                log.info("%s unchanged but committed to since: resetting from snapshot", name)
                self.restore(name)
            else:
                log.info("%s unchanged and clean: reused as is", name)
            return

        started = time.perf_counter()
        self._drop(master, name)
        if dbcfg.dacpac:
            self._publish(dbcfg)
        else:
            master.execute(f"CREATE DATABASE [{name}]")

        with self.connect(name) as db:
            for script in dbcfg.script_files(self.cfg.root):
                run_script(db, script.read_text(encoding="utf-8-sig"), str(script))
            if dbcfg.install_tap:
                for fname, text in tap_scripts():
                    run_script(db, text, f"tap/{fname}")
            seeds = dbcfg.seed_files(self.cfg.root)
            if seeds:
                try:
                    load_files(db, seeds)
                except (BatchError, SeedError) as exc:
                    raise ProvisionError(f"{name}: baseline seed failed: {exc}") from exc
            for script in dbcfg.finalize_files(self.cfg.root):
                run_script(db, script.read_text(encoding="utf-8-sig"), str(script))
            db.execute(
                "EXEC sys.sp_addextendedproperty @name = ?, @value = ?",
                FINGERPRINT_PROPERTY,
                fp,
            )
        if not dbcfg.query_store:
            master.execute(
                f"IF EXISTS (SELECT 1 FROM [{name}].sys.database_query_store_options WHERE actual_state <> 0) "
                f"ALTER DATABASE [{name}] SET QUERY_STORE = OFF"
            )
        self._snapshots[name] = self._create_snapshot(master, name)
        log.info("%s provisioned in %.1fs", name, time.perf_counter() - started)

    def _publish(self, dbcfg: DatabaseConfig) -> None:
        assert self.server is not None and dbcfg.dacpac is not None
        if not dbcfg.dacpac.exists():
            raise ProvisionError(f"dacpac not found: {dbcfg.dacpac} (build the database project first)")
        s = self.server
        target = (
            f"Server={s.host},{s.port};Database={dbcfg.name};User ID={s.user};Password={s.password};"
            "Encrypt=False;TrustServerCertificate=True"
        )
        props = {**DEFAULT_PUBLISH, **dbcfg.publish}
        args = [
            self.cfg.sqlpackage,
            "/Action:Publish",
            f"/SourceFile:{dbcfg.dacpac}",
            f"/TargetConnectionString:{target}",
            *(f"/p:{k}={_prop(v)}" for k, v in props.items()),
            *(f"/v:{k}={v}" for k, v in dbcfg.variables.items()),
        ]
        self._check_sqlpackage_version()
        log.info("publishing %s", dbcfg.dacpac.name)
        try:
            proc = subprocess.run(
                args,
                capture_output=True,
                text=True,
                check=False,
                stdin=subprocess.DEVNULL,
                timeout=self.cfg.publish_timeout,
            )
        except FileNotFoundError as exc:
            raise ProvisionError(
                f"sqlpackage not found ({self.cfg.sqlpackage}): install it with `dotnet tool install -g microsoft.sqlpackage`"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            output = (_text(exc.stdout) + _text(exc.stderr)).strip().splitlines()
            raise ProvisionError(
                f"sqlpackage publish of {dbcfg.name} timed out after {self.cfg.publish_timeout:g}s "
                "(raise publish_timeout, or check the sqlpackage version: TSQLTAP_SQLPACKAGE selects the binary)"
                + (":\n" + "\n".join(output[-40:]) if output else " with no output")
            ) from exc
        if proc.returncode != 0:
            output = (proc.stdout + proc.stderr).strip().splitlines()
            raise ProvisionError(f"sqlpackage publish of {dbcfg.name} failed:\n" + "\n".join(output[-40:]))

    def _check_sqlpackage_version(self) -> None:
        """Once per session, warn if sqlpackage is older than 170 (162.x has been seen to hang)."""
        if self._sqlpackage_checked:
            return
        self._sqlpackage_checked = True
        try:
            proc = subprocess.run(
                [self.cfg.sqlpackage, "/Version"],
                capture_output=True,
                text=True,
                check=False,
                stdin=subprocess.DEVNULL,
                timeout=60,
            )
        except (OSError, subprocess.TimeoutExpired):
            return  # the publish itself reports a missing or stuck binary
        match = re.search(r"(\d+)\.\d+", proc.stdout)
        if match and int(match.group(1)) < MIN_SQLPACKAGE_MAJOR:
            log.warning(
                "sqlpackage %s is older than %d and may hang on publish; put a newer one first on PATH "
                "or point TSQLTAP_SQLPACKAGE at it",
                proc.stdout.strip(),
                MIN_SQLPACKAGE_MAJOR,
            )

    @staticmethod
    def _current_fingerprint(master: Db, name: str) -> str | None:
        if master.scalar("SELECT DB_ID(?)", name) is None:
            return None
        value = master.scalar(
            f"SELECT CAST(value AS NVARCHAR(200)) FROM [{name}].sys.extended_properties WHERE class = 0 AND name = ?",
            FINGERPRINT_PROPERTY,
        )
        return None if value is None else str(value)

    @staticmethod
    def _has_snapshot(master: Db, name: str) -> bool:
        return master.scalar("SELECT DB_ID(?)", name + SNAPSHOT_SUFFIX) is not None

    @staticmethod
    def _drop(master: Db, name: str) -> None:
        snap = name + SNAPSHOT_SUFFIX
        if master.scalar("SELECT DB_ID(?)", snap) is not None:
            master.execute(f"DROP DATABASE [{snap}]")
        if master.scalar("SELECT DB_ID(?)", name) is not None:
            master.execute(f"ALTER DATABASE [{name}] SET SINGLE_USER WITH ROLLBACK IMMEDIATE")
            master.execute(f"DROP DATABASE [{name}]")

    @staticmethod
    def _create_snapshot(master: Db, name: str) -> bool:
        files = master.rows(
            "SELECT name, physical_name FROM sys.master_files WHERE database_id = DB_ID(?) AND type = 0",
            name,
        )
        snap = name + SNAPSHOT_SUFFIX
        specs = []
        for i, f in enumerate(files):
            path = f["physical_name"]
            folder = posixpath.dirname(path) if "/" in path else ntpath.dirname(path)
            sep = "/" if "/" in path else "\\"
            specs.append(f"(NAME = [{f['name']}], FILENAME = '{folder}{sep}{snap}_{i}.ss')")
        try:
            master.execute(f"CREATE DATABASE [{snap}] ON {', '.join(specs)} AS SNAPSHOT OF [{name}]")
        except BatchError as exc:
            log.warning("no snapshot for %s (%s): isolation=snapshot will republish instead", name, exc.message)
            return False
        return True

    def restore(self, name: str) -> None:
        """Put a database back to how it was right after provisioning."""
        dbcfg = self.cfg.database(name)
        self._drop_pooled(dbcfg.name)  # SINGLE_USER WITH ROLLBACK IMMEDIATE would kill it anyway
        master = self.master()
        if not self._snapshots.get(dbcfg.name):
            self._provision_one(master, dbcfg)
            return
        master.execute(f"ALTER DATABASE [{dbcfg.name}] SET SINGLE_USER WITH ROLLBACK IMMEDIATE")
        try:
            master.execute(f"RESTORE DATABASE [{dbcfg.name}] FROM DATABASE_SNAPSHOT = '{dbcfg.name}{SNAPSHOT_SUFFIX}'")
        finally:
            master.execute(f"ALTER DATABASE [{dbcfg.name}] SET MULTI_USER")

    def check_requires(self, requires: list[str] | tuple[str, ...]) -> str | None:
        """The reason to skip, if the server lacks a required capability."""
        missing = sorted({r.lower() for r in requires} - self.capabilities)
        return f"requires {', '.join(missing)} (have: {', '.join(sorted(self.capabilities))})" if missing else None


def _text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    return value.decode(errors="replace") if isinstance(value, bytes) else value


def _prop(value: object) -> str:
    if isinstance(value, bool):
        return "True" if value else "False"
    return str(value)
