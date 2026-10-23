"""Unit tests for Environment._publish's subprocess handling (no Docker needed)."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from tsqltap.config import Config, DatabaseConfig
from tsqltap.connection import ServerInfo
from tsqltap.provision import Environment, ProvisionError


def make_env(tmp_path: Path, **kw: object) -> tuple[Environment, DatabaseConfig]:
    dacpac = tmp_path / "Shop.dacpac"
    dacpac.write_bytes(b"x")
    db = DatabaseConfig(name="Shop", dacpac=dacpac)
    env = Environment(Config(root=tmp_path, databases=[db], **kw))  # type: ignore[arg-type]
    env.server = ServerInfo("127.0.0.1", 1433, "sa", "pw")
    return env, db


def fake_run(version: str = "170.5.96\n", publish_exc: Exception | None = None):
    calls: list[tuple[list[str], dict]] = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        if args[-1] == "/Version":
            return SimpleNamespace(returncode=0, stdout=version, stderr="")
        if publish_exc:
            raise publish_exc
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    return run, calls


def test_publish_detaches_stdin_and_sets_timeout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env, db = make_env(tmp_path, publish_timeout=42)
    run, calls = fake_run()
    monkeypatch.setattr(subprocess, "run", run)
    env._publish(db)
    publish = next(kw for args, kw in calls if "/Action:Publish" in args)
    assert publish["stdin"] == subprocess.DEVNULL
    assert publish["timeout"] == 42


def test_publish_timeout_raises_with_database_and_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env, db = make_env(tmp_path, publish_timeout=5)
    exc = subprocess.TimeoutExpired(["sqlpackage"], 5, output=b"line1\nlast line\n", stderr=None)
    run, _ = fake_run(publish_exc=exc)
    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(ProvisionError) as info:
        env._publish(db)
    msg = str(info.value)
    assert "Shop" in msg and "timed out after 5s" in msg and "last line" in msg


def test_old_sqlpackage_warns_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    env, db = make_env(tmp_path)
    run, calls = fake_run(version="162.1.167\n")
    monkeypatch.setattr(subprocess, "run", run)
    with caplog.at_level(logging.WARNING, logger="tsqltap"):
        env._publish(db)
        env._publish(db)
    warnings = [r for r in caplog.records if "TSQLTAP_SQLPACKAGE" in r.getMessage()]
    assert len(warnings) == 1
    assert sum(1 for args, _ in calls if args[-1] == "/Version") == 1


def test_new_sqlpackage_does_not_warn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    env, db = make_env(tmp_path)
    run, _ = fake_run()
    monkeypatch.setattr(subprocess, "run", run)
    with caplog.at_level(logging.WARNING, logger="tsqltap"):
        env._publish(db)
    assert not caplog.records
