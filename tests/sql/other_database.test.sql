-- tsqltap: database=TsqltapOther
/* Runs in the second configured database, which has no schema of its own -- but still has tap. */
DECLARE @db SYSNAME = DB_NAME();

EXEC tap.[is] @db, N'TsqltapOther', N'running in the database named by the directive';
EXEC tap.hasnt_table N'dbo.Trust', N'the first database''s tables are not here';
