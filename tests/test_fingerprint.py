"""Unit tests for Environment.fingerprint and the --tsqltap-keep reuse check (no Docker needed)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tsqltap.config import Config, DatabaseConfig
from tsqltap.provision import Environment


def make_env(tmp_path: Path, *, keep: bool = False) -> Environment:
    for name in ("A", "B", "C"):
        (tmp_path / f"{name}.dacpac").write_bytes(f"{name}1".encode())
    dbs = [DatabaseConfig(name=n, dacpac=tmp_path / f"{n}.dacpac") for n in ("A", "B", "C")]
    return Environment(Config(root=tmp_path, databases=dbs), keep=keep)


def fingerprints(env: Environment) -> dict[str, str]:
    return {d.name: env.fingerprint(d) for d in env.cfg.databases}


def test_changing_a_database_changes_every_later_one(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    before = fingerprints(env)
    (tmp_path / "A.dacpac").write_bytes(b"A2")
    after = fingerprints(env)
    assert all(after[n] != before[n] for n in "ABC")


def test_changing_a_later_database_leaves_earlier_ones_alone(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    before = fingerprints(env)
    (tmp_path / "B.dacpac").write_bytes(b"B2")
    after = fingerprints(env)
    assert after["A"] == before["A"]
    assert after["B"] != before["B"]
    assert after["C"] != before["C"]


def test_fingerprint_is_stable(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    assert fingerprints(env) == fingerprints(env)


class FakeMaster:
    def execute(self, *a: Any, **k: Any) -> None:
        pass


def test_keep_rebuilds_dependent_after_earlier_database_changed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = make_env(tmp_path, keep=True)
    stored = fingerprints(env)  # what the kept container recorded last session
    rebuilt: list[str] = []

    monkeypatch.setattr(Environment, "_current_fingerprint", staticmethod(lambda master, name: stored[name]))
    monkeypatch.setattr(Environment, "_has_snapshot", staticmethod(lambda master, name: True))
    monkeypatch.setattr(Environment, "_is_dirty", staticmethod(lambda master, name: False))
    # Stop at the rebuild: record it instead of publishing.
    monkeypatch.setattr(
        Environment, "_drop", lambda self, master, name: rebuilt.append(name) or (_ for _ in ()).throw(StopIteration)
    )

    for db in env.cfg.databases:  # nothing changed: all reused
        env._provision_one(FakeMaster(), db)  # type: ignore[arg-type]
    assert rebuilt == []

    (tmp_path / "A.dacpac").write_bytes(b"A2")
    for name in "ABC":
        with pytest.raises(StopIteration):
            env._provision_one(FakeMaster(), env.cfg.database(name))  # type: ignore[arg-type]
    assert rebuilt == ["A", "B", "C"]
