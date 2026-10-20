"""`tsqltap script` emits the same library the runner installs, as one standalone script."""

from pathlib import Path

from tsqltap.cli import main, render_tap_sql
from tsqltap.connection import Db
from tsqltap.provision import Environment, run_script, tap_scripts
from tsqltap.sqlfile import split_batches


def test_script_has_every_installed_batch() -> None:
    script_batches = split_batches(render_tap_sql())
    for name, text in tap_scripts():
        for batch in split_batches(text):
            assert batch in script_batches, name


def test_script_command_writes_file(tmp_path: Path) -> None:
    out = tmp_path / "tap.sql"
    assert main(["script", "-o", str(out)]) == 0
    assert out.read_text(encoding="utf-8") == render_tap_sql()


def test_script_installs_and_works_in_a_fresh_database(mssql_env: Environment) -> None:
    with mssql_env.connect("master") as master:
        master.execute("IF DB_ID(N'TapScriptCheck') IS NOT NULL DROP DATABASE TapScriptCheck")
        master.execute("CREATE DATABASE TapScriptCheck")
    try:
        with mssql_env.connect("TapScriptCheck") as db:
            assert isinstance(db, Db)
            run_script(db, render_tap_sql(), "tap.sql")
            run_script(db, render_tap_sql(), "tap.sql (second run)")  # re-runnable
            sets = db.run("EXEC tap.[is] 1, 1, N'standalone install works'")
            assert sets
    finally:
        with mssql_env.connect("master") as master:
            master.execute("ALTER DATABASE TapScriptCheck SET SINGLE_USER WITH ROLLBACK IMMEDIATE")
            master.execute("DROP DATABASE TapScriptCheck")
