"""Config resolution -- no database needed."""

from pathlib import Path

from tsqltap.config import Config, DatabaseConfig


def _cfg(root: Path) -> Config:
    return Config(
        root=root,
        databases=[
            DatabaseConfig("Core"),
            DatabaseConfig("App", tests=["DbProjects/App"]),
            DatabaseConfig("AppAudit", tests=["DbProjects/App/Tests/Audit/**"]),
            DatabaseConfig("Tx", tests=["**/Transactions/*.test.sql"]),
        ],
    )


def test_database_for_by_folder(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    assert cfg.database_for(tmp_path / "DbProjects/App/Tests/x.test.sql").name == "App"
    assert cfg.database_for(tmp_path / "DbProjects/App/Tests/Audit/y.test.sql").name == "AppAudit"  # most specific
    assert cfg.database_for(tmp_path / "Db/Transactions/z.test.sql").name == "Tx"
    assert cfg.database_for(tmp_path / "elsewhere/w.test.sql").name == "Core"  # first database
    assert cfg.database_for(tmp_path.parent / "outside.test.sql").name == "Core"


def test_explicit_database_wins(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    assert cfg.database_for(tmp_path / "DbProjects/App/x.test.sql", "tx").name == "Tx"


def test_password_is_random_unless_kept_or_configured(tmp_path: Path) -> None:
    from tsqltap.provision import KEPT_PASSWORD, Environment

    a, b = Environment(Config(root=tmp_path)), Environment(Config(root=tmp_path))
    assert a.password != b.password, "a throwaway container gets its own password"
    assert Environment(Config(root=tmp_path), keep=True).password == KEPT_PASSWORD
    assert Environment(Config(root=tmp_path, password="Mine!1aA"), keep=True).password == "Mine!1aA"
