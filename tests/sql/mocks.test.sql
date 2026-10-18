/* fake_table and spy_procedure, all passing. */
SET NOCOUNT ON;

-- Region is not empty (baseline seed), and Trust's foreign key would demand a real region.
EXEC tap.fake_table N'dbo.Region';
EXEC tap.is_empty N'SELECT * FROM dbo.Region', N'faked table starts empty';

EXEC tap.fake_table N'dbo.Trust';

INSERT dbo.Trust (TrustCode, RegionCode, Name, OpenedOn) VALUES ('T1', 'NOWHERE', N'One', '2026-01-01');

EXEC tap.row_count_is N'SELECT * FROM dbo.Trust', 1, N'foreign key dropped by default';
EXEC tap.throws_ok N'INSERT dbo.Trust (TrustCode, RegionCode, Name, OpenedOn, Score) VALUES (''T2'', ''X'', N''x'', ''2026-01-01'', 500)'
	, 547, N'%CK_Trust_Score%', N'CHECK constraint kept, with its name';
EXEC tap.throws_ok N'INSERT dbo.Trust (TrustCode, RegionCode, Name, OpenedOn) VALUES (''T1'', ''X'', N''dup'', ''2026-01-01'')'
	, 2627, NULL, N'UNIQUE constraint kept';
EXEC tap.results_eq N'SELECT IsActive FROM dbo.Trust', N'VALUES (CAST(1 AS BIT))', N'DEFAULT kept';
EXEC tap.row_count_is N'SELECT * FROM dbo.TrustAudit WHERE TrustCode = ''T1''', 1, N'trigger kept';

-- Spy on the notifier: usp_AddTrust should call it once with the new trust's code.
EXEC tap.spy_procedure N'dbo.usp_Notify';
EXEC dbo.usp_AddTrust @TrustCode = 'T3', @RegionCode = 'X', @Name = N'Three';

EXEC tap.results_eq N'SELECT Message, Level FROM tap.[SpyLog_dbo_usp_Notify]'
	, N'VALUES (N''added T3'', NULL)'
	, N'spy saw one call; omitted parameters log as NULL';
EXEC tap.is_empty N'SELECT * FROM dbo.Notification', N'the real procedure did not run';
