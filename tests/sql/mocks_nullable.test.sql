/* fake_table: @keep_not_null, @keep_identity, @tsqlt_compatible. */
SET NOCOUNT ON;

-- Defaults are unchanged: NOT NULL and IDENTITY are both kept.
EXEC tap.fake_table N'dbo.Notification';
EXEC tap.throws_ok N'INSERT dbo.Notification (Message) VALUES (N''x'')', 515, NULL, N'default keeps NOT NULL';
EXEC tap.throws_ok N'INSERT dbo.Notification (NotificationId, Message, Level) VALUES (5, N''x'', 1)', 544, NULL, N'default keeps IDENTITY';

-- @keep_not_null = 0: a partial INSERT works. The identity column is left alone (it cannot be NULLable).
EXEC tap.fake_table N'dbo.Trust', @keep_not_null = 0;
INSERT dbo.Trust (TrustCode) VALUES ('T1');
EXEC tap.row_count_is N'SELECT * FROM dbo.Trust', 1, N'partial insert succeeds with @keep_not_null = 0';
EXEC tap.row_count_is N'SELECT * FROM dbo.Trust WHERE TrustId = 1', 1, N'identity still generates ids';
EXEC tap.row_count_is N'SELECT 1 WHERE COLUMNPROPERTY(OBJECT_ID(N''dbo.Trust''), N''Name'', ''AllowsNull'') = 1', 1, N'ordinary column nullable';

-- PK interaction: with @keep_keys = 1 (default) the PK column stays NOT NULL.
EXEC tap.fake_table N'dbo.Region', @keep_not_null = 0;
EXEC tap.throws_ok N'INSERT dbo.Region (Name) VALUES (N''x'')', 515, NULL, N'kept PK column stays NOT NULL';
INSERT dbo.Region (RegionCode) VALUES ('R1');
EXEC tap.row_count_is N'SELECT * FROM dbo.Region', 1, N'non-key columns nullable beside a kept PK';

-- @keep_identity = 0: explicit ids without IDENTITY_INSERT; everything else is kept.
EXEC tap.fake_table N'dbo.TrustAudit', @keep_identity = 0;
INSERT dbo.TrustAudit (AuditId, TrustCode, Action) VALUES (42, 'T1', 'x');
EXEC tap.row_count_is N'SELECT * FROM dbo.TrustAudit WHERE AuditId = 42', 1, N'explicit id inserted';
EXEC tap.throws_ok N'INSERT dbo.TrustAudit (AuditId, TrustCode) VALUES (43, ''T1'')', 515, NULL, N'NOT NULL still kept';
EXEC tap.throws_ok N'INSERT dbo.TrustAudit (AuditId, TrustCode, Action) VALUES (42, ''T1'', ''x'')', 2627, NULL, N'PK still kept';

-- @keep_not_null = 0 with @keep_keys = 0: the PK column is nullable too.
CREATE TABLE dbo.TapFakeKeyed (Code VARCHAR(5) NOT NULL PRIMARY KEY, Note NVARCHAR(20) NOT NULL);
EXEC tap.fake_table N'dbo.TapFakeKeyed', @keep_not_null = 0, @keep_keys = 0;
INSERT dbo.TapFakeKeyed (Note) VALUES (N'x');
EXEC tap.row_count_is N'SELECT * FROM dbo.TapFakeKeyed WHERE Code IS NULL', 1, N'PK column nullable when @keep_keys = 0';

-- @tsqlt_compatible = 1: nothing is kept.
CREATE TABLE dbo.TapFakeAll (
	Id INT IDENTITY(1, 1) NOT NULL PRIMARY KEY
	,Code VARCHAR(5) NOT NULL UNIQUE
	,Score INT NOT NULL DEFAULT (7) CHECK (Score < 10)
	);
EXEC tap.fake_table N'dbo.TapFakeAll', @tsqlt_compatible = 1;
INSERT dbo.TapFakeAll (Id, Code, Score) VALUES (1, 'A', 500), (1, 'A', NULL);
INSERT dbo.TapFakeAll (Code) VALUES ('B');
EXEC tap.row_count_is N'SELECT * FROM dbo.TapFakeAll', 3, N'no identity, NOT NULL, PK, UNIQUE or CHECK';
EXEC tap.row_count_is N'SELECT * FROM dbo.TapFakeAll WHERE Id IS NULL AND Score IS NULL', 1, N'default dropped, columns nullable';
