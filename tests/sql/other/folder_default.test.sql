/* No database directive: the folder (tests = ["tests/sql/other"] in config) picks TsqltapOther. */
DECLARE @db SYSNAME = DB_NAME();

EXEC tap.[is] @db, N'TsqltapOther', N'folder default database';
