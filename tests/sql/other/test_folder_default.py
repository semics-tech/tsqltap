from tsqltap.connection import Db


def test_python_tests_get_the_folder_default_too(mssql_db: Db) -> None:
    assert mssql_db.scalar("SELECT DB_NAME()") == "TsqltapOther"
