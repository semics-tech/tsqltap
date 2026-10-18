"""Parsing only -- no database needed."""

import pytest

from tsqltap.sqlfile import DirectiveError, parse_directives, split_batches


def test_defaults() -> None:
    d = parse_directives("SELECT 1", "a.test.sql")
    assert d.isolation == "transaction"
    assert d.database is None
    assert d.requires == []
    assert d.seeds == []


def test_all_directives() -> None:
    d = parse_directives(
        "-- tsqltap: isolation=snapshot; database=Core\n"
        "--tsqltap: requires=CLR, agent\n"
        "-- TSQLTAP: seed=a.csv\n"
        "-- tsqltap: seed=b/*.yaml\n"
        "SELECT 1",
        "x.test.sql",
    )
    assert d.isolation == "snapshot"
    assert d.database == "Core"
    assert d.requires == ["clr", "agent"]
    assert d.seeds == ["a.csv", "b/*.yaml"]


def test_legacy_files_default_to_no_isolation() -> None:
    assert parse_directives("SELECT 1", "Notification.Tests.sql").isolation == "none"
    assert parse_directives("-- tsqltap: isolation=transaction", "N.Tests.sql").isolation == "transaction"


def test_tsqlt_defaults_to_no_isolation_unless_set() -> None:
    assert parse_directives("-- tsqltap: tsqlt=all", "t.test.sql").isolation == "none"
    assert parse_directives("-- tsqltap: isolation=transaction; tsqlt=Cls", "t.test.sql").isolation == "transaction"
    assert parse_directives("-- tsqltap: tsqlt=Cls; isolation=transaction", "t.test.sql").isolation == "transaction"


@pytest.mark.parametrize(
    "text",
    ["-- tsqltap: isolation=serializable", "-- tsqltap: colour=blue", "-- tsqltap: isolation"],
)
def test_bad_directives(text: str) -> None:
    with pytest.raises(DirectiveError):
        parse_directives(text, "a.test.sql")


def test_split_batches() -> None:
    text = "SELECT 1\nGO\n  go  \nSELECT 2\nGO -- trailing comment\nSELECT 'GO'\n"
    assert split_batches(text) == ["SELECT 1", "SELECT 2", "SELECT 'GO'"]


def test_restore_directive() -> None:
    d = parse_directives("-- tsqltap: isolation=snapshot; restore=App, Tx", "a.test.sql")
    assert d.restore == ["App", "Tx"]
    with pytest.raises(DirectiveError, match="only applies with isolation=snapshot"):
        parse_directives("-- tsqltap: restore=App", "a.test.sql")
