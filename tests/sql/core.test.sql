/* Basic tap assertions, all passing. */
SET NOCOUNT ON;

EXEC tap.ok 1, N'ok passes on 1';
EXEC tap.[is] 1, 1, N'is: equal ints';
DECLARE @big BIGINT = 1;
EXEC tap.[is] @big, 1, N'is: int equals bigint';
EXEC tap.[is] NULL, NULL, N'is: NULL is NULL';
EXEC tap.isnt N'a', N'b', N'isnt: different strings';
EXEC tap.isnt 1, NULL, N'isnt: value vs NULL';
EXEC tap.matches N'hello world', N'hello%', N'matches: LIKE pattern';
EXEC tap.pass N'pass passes';
EXEC tap.diag N'a diagnostic line, never a failure';

EXEC tap.throws_ok N'SELECT 1/0', 8134, NULL, N'throws_ok: divide by zero by number';
EXEC tap.throws_ok N'THROW 50010, ''custom failure'', 1', NULL, N'%custom%', N'throws_ok: by message';
EXEC tap.throws_ok N'INSERT dbo.Trust (TrustCode, RegionCode, Name, OpenedOn, Score) VALUES (''T9'', ''N1'', N''x'', ''2026-01-01'', 200)'
	, 547, N'%CK_Trust_Score%', N'throws_ok: CHECK violation';
EXEC tap.lives_ok N'SELECT 1', N'lives_ok: harmless statement';

-- The CHECK violation above neither doomed nor ended the transaction the test runs in.
DECLARE @tc INT = @@TRANCOUNT;
EXEC tap.[is] @tc, 1, N'still inside the test transaction after a caught error';
