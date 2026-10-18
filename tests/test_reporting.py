"""End to end through pytest itself: outcomes, failure text and JUnit XML for SQL test files.

Runs an inner pytest session (in-process, so it starts its own container: this test is slow).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

PYPROJECT = """
[tool.tsqltap]
image = "mcr.microsoft.com/mssql/server:2025-latest"

[[tool.tsqltap.databases]]
name = "Inner"
scripts = ["schema.sql"]
"""


def test_sql_files_through_pytest(pytester: pytest.Pytester) -> None:
    pytester.makefile(".toml", pyproject=PYPROJECT)
    pytester.makefile(".sql", schema="CREATE TABLE dbo.T (n INT NOT NULL);")
    pytester.path.joinpath("good.test.sql").write_text(
        "EXEC tap.pass N'first';\nEXEC tap.ok 1, N'second';\n", encoding="utf-8"
    )
    pytester.path.joinpath("bad.test.sql").write_text(
        "EXEC tap.pass N'fine';\nEXEC tap.[is] 1, 2, N'one is two';\n", encoding="utf-8"
    )
    pytester.path.joinpath("empty.test.sql").write_text("SELECT 1;\n", encoding="utf-8")
    pytester.path.joinpath("leaky.test.sql").write_text(
        "INSERT dbo.T VALUES (1);\nCOMMIT;\nEXEC tap.pass N'after commit';\n", encoding="utf-8"
    )
    pytester.path.joinpath("z_check_clean.test.sql").write_text(
        "EXEC tap.is_empty N'SELECT * FROM dbo.T', N'leak was cleaned up';\n", encoding="utf-8"
    )

    pytester.path.joinpath("todo_open.test.sql").write_text(
        "EXEC tap.todo_start N'bug 1';\nEXEC tap.fail N'known';\nEXEC tap.todo_end;\nEXEC tap.pass N'fine too';\n",
        encoding="utf-8",
    )
    pytester.path.joinpath("todo_fixed.test.sql").write_text(
        "EXEC tap.todo_start N'bug 2';\nEXEC tap.pass N'now passes';\nEXEC tap.todo_end;\n", encoding="utf-8"
    )

    junit = pytester.path / "junit.xml"
    result = pytester.runpytest_inprocess("-p", "no:cacheprovider", f"--junitxml={junit}")

    result.stdout.fnmatch_lines(["*bad*[[]one is two[]]*", "have: '1' (int)", "want: '2' (int)"])
    result.stdout.fnmatch_lines(["SUBFAILED*bad.test.sql::bad::one is two"])
    result.stdout.fnmatch_lines(["*FAILED*empty.test.sql::empty*no assertions ran*"])
    result.stdout.fnmatch_lines(["*FAILED*leaky.test.sql::leaky*ended the transaction*"])
    result.stdout.fnmatch_lines(["TODO passed: bug 2", "The known failure is fixed*"])
    result.stdout.fnmatch_lines(["SUBFAILED*todo_fixed.test.sql::todo_fixed::now passes*"])
    result.stdout.no_fnmatch_line("FAILED*todo_open.test.sql::todo_open *")

    cases = {(c.get("classname"), c.get("name")): c for c in ET.parse(junit).getroot().iter("testcase")}
    names = {name for _, name in cases}
    assert {"first", "second", "fine", "one is two", "leak was cleaned up"} <= names
    failing = {name for (_, name), c in cases.items() if c.find("failure") is not None}
    assert "one is two" in failing
    assert "fine" not in failing and "first" not in failing
    assert "leak was cleaned up" not in failing


def test_collect_error_for_bad_directive(pytester: pytest.Pytester) -> None:
    pytester.makefile(".toml", pyproject=PYPROJECT)
    Path(pytester.path, "x.test.sql").write_text("-- tsqltap: isolation=sometimes\nSELECT 1", encoding="utf-8")
    result = pytester.runpytest_inprocess("-p", "no:cacheprovider", "--collect-only")
    result.stdout.fnmatch_lines(["*isolation must be one of*"])


def test_a_tests_folder_hidden_by_norecursedirs_is_an_error(pytester: pytest.Pytester) -> None:
    pytester.makefile(".toml", pyproject=PYPROJECT + 'tests = ["db/Tests/Build"]\n')
    result = pytester.runpytest_inprocess("-p", "no:cacheprovider", "--collect-only")
    result.stderr.fnmatch_lines(["*norecursedirs pattern 'build' matches the folder 'Build'*"])
