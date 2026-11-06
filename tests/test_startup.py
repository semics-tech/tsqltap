"""Unit tests for engine reachability and the container start deadline (no container needed)."""

from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from tsqltap.config import Config, load
from tsqltap.provision import Environment, ProvisionError, check_engine


class FakeContainer:
    def __init__(self, start) -> None:
        self._start = start
        self._container = SimpleNamespace(id="abc")

    def start(self) -> None:
        self._start()


def fake_engine(monkeypatch: pytest.MonkeyPatch, ping=None, logs=b"line1\nOut of memory\n") -> list[bool]:
    removed: list[bool] = []

    class Containers:
        def get(self, cid):
            return SimpleNamespace(logs=lambda tail: logs, remove=lambda force: removed.append(force))

    client = SimpleNamespace(ping=ping or (lambda: True), containers=Containers())
    import docker

    monkeypatch.setattr(docker, "from_env", lambda **kw: client)
    return removed


def env(tmp_path: Path, **kw: object) -> Environment:
    return Environment(Config(root=tmp_path, **kw))  # type: ignore[arg-type]


def unreachable() -> None:
    raise ConnectionError("no such file /var/run/docker.sock")


def test_check_engine_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_engine(monkeypatch, ping=unreachable)
    with pytest.raises(ProvisionError) as info:
        check_engine()
    msg = str(info.value)
    assert "Docker is not reachable" in msg and "docker.sock" in msg and "Start Docker Desktop" in msg


def test_start_checks_engine_first(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_engine(monkeypatch, ping=unreachable)
    with pytest.raises(ProvisionError, match="not reachable"):
        env(tmp_path).start()


def test_hung_start_times_out_with_logs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    removed = fake_engine(monkeypatch)
    release = threading.Event()
    container = FakeContainer(lambda: release.wait(10))
    try:
        with pytest.raises(ProvisionError) as info:
            env(tmp_path, startup_timeout=0.2)._start_with_deadline(container)  # type: ignore[arg-type]
    finally:
        release.set()
    msg = str(info.value)
    assert "not ready after 0.2s" in msg and "memory" in msg and "TSQLTAP_STARTUP_TIMEOUT" in msg
    assert "Out of memory" in msg
    assert removed == [True]


def test_readiness_timeout_becomes_provision_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_engine(monkeypatch)

    def fail() -> None:
        raise TimeoutError("Wait time (120s) exceeded")

    with pytest.raises(ProvisionError, match="did not become ready"):
        env(tmp_path, startup_timeout=5)._start_with_deadline(FakeContainer(fail))  # type: ignore[arg-type]


def test_other_start_errors_propagate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_engine(monkeypatch)

    def fail() -> None:
        raise RuntimeError("pull access denied")

    with pytest.raises(RuntimeError, match="pull access denied"):
        env(tmp_path, startup_timeout=5)._start_with_deadline(FakeContainer(fail))  # type: ignore[arg-type]


def test_startup_timeout_default_and_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert Config(root=tmp_path).startup_timeout == 900
    (tmp_path / "pyproject.toml").write_text(
        '[tool.tsqltap]\nstartup_timeout = 300\n\n[[tool.tsqltap.databases]]\nname = "A"\n'
    )
    assert load(tmp_path).startup_timeout == 300
    monkeypatch.setenv("TSQLTAP_STARTUP_TIMEOUT", "42")
    assert load(tmp_path).startup_timeout == 42
