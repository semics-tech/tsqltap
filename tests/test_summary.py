"""The console summary line -- no database needed."""

from collections import Counter

from tsqltap.plugin import format_summary


def test_no_sql_files_prints_nothing() -> None:
    assert format_summary(Counter()) is None


def test_counts_all_outcomes() -> None:
    totals = Counter(files=3, passed=38, failed=1, todo=2)
    assert format_summary(totals) == "tsqltap: 3 files, 41 assertions (38 passed, 1 failed, 2 todo)"


def test_singular_and_omits_zero_outcomes() -> None:
    assert format_summary(Counter(files=1, passed=1)) == "tsqltap: 1 file, 1 assertion (1 passed)"


def test_file_with_no_assertions() -> None:
    assert format_summary(Counter(files=1)) == "tsqltap: 1 file, 0 assertions"
