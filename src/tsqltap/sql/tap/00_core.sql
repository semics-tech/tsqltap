/*
tap -- pgTAP-style assertions for T-SQL, installed by tsqltap into the TEST database only.

Every assertion streams ONE row to the client:

    (__tap, kind, ok, description, detail)

rather than writing to a table, so results survive a ROLLBACK inside the code under test. The
tsqltap runner collects these rows; each one becomes a pytest subtest / JUnit case.
*/
IF SCHEMA_ID(N'tap') IS NULL
	EXEC (N'CREATE SCHEMA tap AUTHORIZATION dbo');
GO

CREATE OR ALTER PROCEDURE tap._emit
	@ok BIT
	,@description NVARCHAR(4000)
	,@detail NVARCHAR(MAX) = NULL
	,@kind VARCHAR(10) = 'assert'
AS
SET NOCOUNT ON;

-- Inside tap.todo_start / tap.todo_end an assertion is a TODO: expected to fail, with a reason.
DECLARE @todo NVARCHAR(4000) = CONVERT(NVARCHAR(4000), SESSION_CONTEXT(N'tap.todo'));

IF @todo IS NOT NULL AND @kind = 'assert'
	SET @kind = 'todo';

SELECT CAST(1 AS BIT) AS __tap
	,@kind AS kind
	,@ok AS ok
	,ISNULL(@description, N'') AS description
	,@detail AS detail
	,CASE WHEN @kind = 'todo' THEN @todo END AS todo;
GO

/*
Mark the assertions that follow as known failures (pgTAP's TODO), until tap.todo_end:

    EXEC tap.todo_start N'BUG-123: CATCH rolls back a transaction it never opened';
    EXEC tap.throws_ok N'EXEC Config.usp_insertCensusDate', 50000, NULL, N'the real error surfaces';
    EXEC tap.todo_end;

A failing TODO is reported as an expected failure and does not fail the run. A passing one DOES fail
it, with a reminder to remove the TODO -- so the test starts guarding the fix as soon as it lands.
Kept in SESSION_CONTEXT, which a ROLLBACK in the code under test does not undo.
*/
CREATE OR ALTER PROCEDURE tap.todo_start
	@why NVARCHAR(4000)
AS
EXEC sys.sp_set_session_context @key = N'tap.todo', @value = @why;
GO

CREATE OR ALTER PROCEDURE tap.todo_end
AS
EXEC sys.sp_set_session_context @key = N'tap.todo', @value = NULL;
GO

CREATE OR ALTER FUNCTION tap.display (@value SQL_VARIANT)
RETURNS NVARCHAR(4000)
AS
BEGIN
	RETURN CASE
			WHEN @value IS NULL THEN N'NULL'
			WHEN SQL_VARIANT_PROPERTY(@value, 'BaseType') IN ('datetime', 'datetime2', 'smalldatetime', 'date', 'time', 'datetimeoffset')
				THEN CONVERT(NVARCHAR(4000), @value, 126)
			ELSE N'''' + CONVERT(NVARCHAR(4000), @value) + N''' (' + CONVERT(NVARCHAR(128), SQL_VARIANT_PROPERTY(@value, 'BaseType')) + N')'
			END;
END;
GO

CREATE OR ALTER FUNCTION tap.format_type (
	@type SYSNAME
	,@max_length SMALLINT
	,@precision TINYINT
	,@scale TINYINT
	)
RETURNS NVARCHAR(200)
AS
BEGIN
	RETURN @type + CASE
			WHEN @type IN (N'varchar', N'char', N'varbinary', N'binary')
				THEN N'(' + CASE WHEN @max_length = -1 THEN N'max' ELSE CAST(@max_length AS NVARCHAR(10)) END + N')'
			WHEN @type IN (N'nvarchar', N'nchar')
				THEN N'(' + CASE WHEN @max_length = -1 THEN N'max' ELSE CAST(@max_length / 2 AS NVARCHAR(10)) END + N')'
			WHEN @type IN (N'decimal', N'numeric')
				THEN N'(' + CAST(@precision AS NVARCHAR(10)) + N',' + CAST(@scale AS NVARCHAR(10)) + N')'
			WHEN @type IN (N'datetime2', N'time', N'datetimeoffset')
				THEN N'(' + CAST(@scale AS NVARCHAR(10)) + N')'
			ELSE N''
			END;
END;
GO

/* ------------------------------------------------------------------ basic assertions */

CREATE OR ALTER PROCEDURE tap.ok
	@condition BIT
	,@description NVARCHAR(4000) = NULL
AS
SET NOCOUNT ON;

DECLARE @ok BIT = ISNULL(@condition, 0);
DECLARE @detail NVARCHAR(MAX) = CASE WHEN @condition IS NULL THEN N'condition was NULL' END;

EXEC tap._emit @ok, @description, @detail;
GO

CREATE OR ALTER PROCEDURE tap.pass
	@description NVARCHAR(4000) = NULL
AS
EXEC tap._emit 1, @description;
GO

CREATE OR ALTER PROCEDURE tap.fail
	@description NVARCHAR(4000) = NULL
	,@detail NVARCHAR(MAX) = NULL
AS
EXEC tap._emit 0, @description, @detail;
GO

-- A note attached to the test's output; never fails anything.
CREATE OR ALTER PROCEDURE tap.diag
	@message NVARCHAR(MAX)
AS
EXEC tap._emit NULL, N'diag', @message, 'diag';
GO

-- Skip the rest of the file: the runner reports the whole file as skipped.
CREATE OR ALTER PROCEDURE tap.skip_all
	@reason NVARCHAR(4000)
AS
EXEC tap._emit NULL, @reason, NULL, 'skip';
GO

-- NULL-safe equality. Compares as SQL_VARIANT, so 1 (int) equals 1 (bigint) but '1' does not equal 1.
CREATE OR ALTER PROCEDURE tap.[is]
	@have SQL_VARIANT
	,@want SQL_VARIANT
	,@description NVARCHAR(4000) = NULL
AS
SET NOCOUNT ON;

DECLARE @ok BIT = CASE
		WHEN @have IS NULL AND @want IS NULL THEN 1
		WHEN @have = @want
			AND SQL_VARIANT_PROPERTY(@have, 'BaseType') IN ('varchar', 'nvarchar', 'char', 'nchar')
			AND CONVERT(NVARCHAR(4000), @have) COLLATE Latin1_General_BIN2 <> CONVERT(NVARCHAR(4000), @want) COLLATE Latin1_General_BIN2
			THEN 0 -- strings compare exactly, not under the database's case-insensitive collation
		WHEN @have = @want THEN 1
		ELSE 0
		END;

DECLARE @detail NVARCHAR(MAX) = CASE WHEN @ok = 0 THEN N'have: ' + tap.display(@have) + NCHAR(10) + N'want: ' + tap.display(@want) END;

EXEC tap._emit @ok, @description, @detail;
GO

CREATE OR ALTER PROCEDURE tap.isnt
	@have SQL_VARIANT
	,@want SQL_VARIANT
	,@description NVARCHAR(4000) = NULL
AS
SET NOCOUNT ON;

DECLARE @ok BIT = CASE
		WHEN @have IS NULL AND @want IS NULL THEN 0
		WHEN @have IS NULL OR @want IS NULL THEN 1
		WHEN @have = @want THEN 0
		ELSE 1
		END;

DECLARE @detail NVARCHAR(MAX) = CASE WHEN @ok = 0 THEN N'both are: ' + tap.display(@have) END;

EXEC tap._emit @ok, @description, @detail;
GO

-- LIKE pattern match.
CREATE OR ALTER PROCEDURE tap.matches
	@have NVARCHAR(MAX)
	,@pattern NVARCHAR(4000)
	,@description NVARCHAR(4000) = NULL
AS
SET NOCOUNT ON;

DECLARE @ok BIT = CASE WHEN @have LIKE @pattern THEN 1 ELSE 0 END;

DECLARE @detail NVARCHAR(MAX) = CASE WHEN @ok = 0 THEN N'have: ' + ISNULL(N'''' + LEFT(@have, 3900) + N'''', N'NULL') + NCHAR(10) + N'does not match: ' + @pattern END;

EXEC tap._emit @ok, @description, @detail;
GO

/* ------------------------------------------------------------------ errors */

/*
Run @sql and pass if it raises. @errnum and @msg_like (a LIKE pattern) narrow what counts.

Inside a transaction the statement runs under a savepoint that is rolled back after the error, so
a failing multi-statement @sql leaves no partial work behind. XACT_ABORT is turned off for the
duration (SET options in a procedure revert when it returns) so a constraint violation does not
doom the test's transaction.
*/
CREATE OR ALTER PROCEDURE tap.throws_ok
	@sql NVARCHAR(MAX)
	,@errnum INT = NULL
	,@msg_like NVARCHAR(4000) = NULL
	,@description NVARCHAR(4000) = NULL
AS
SET NOCOUNT ON;
SET XACT_ABORT OFF;

DECLARE @savepoint BIT = 0
	,@trancount INT = @@TRANCOUNT;

IF @trancount > 0
BEGIN
	SAVE TRANSACTION tap_throws_ok;
	SET @savepoint = 1;
END;

BEGIN TRY
	EXEC sys.sp_executesql @sql;
END TRY
BEGIN CATCH
	DECLARE @n INT = ERROR_NUMBER()
		,@m NVARCHAR(4000) = ERROR_MESSAGE()
		,@state INT = XACT_STATE();

	-- Code that opens its own transaction and then raises (typically with XACT_ABORT ON) would have
	-- had it rolled back by the uncaught error. Catching the error here leaves it open instead, so roll
	-- it back -- otherwise this proc returns with a different @@TRANCOUNT and raises 266.
	IF @trancount = 0 AND @@TRANCOUNT > 0
	BEGIN
		ROLLBACK TRANSACTION;
		SET @state = 0;
	END
	ELSE IF @savepoint = 1 AND @state = 1
		ROLLBACK TRANSACTION tap_throws_ok;

	DECLARE @ok BIT = CASE
			WHEN @errnum IS NOT NULL AND @n <> @errnum THEN 0
			WHEN @msg_like IS NOT NULL AND @m NOT LIKE @msg_like THEN 0
			ELSE 1
			END;
	DECLARE @detail NVARCHAR(MAX) = CASE
			WHEN @ok = 0 THEN N'raised: ' + CAST(@n AS NVARCHAR(10)) + N' ' + @m + NCHAR(10) + N'wanted: '
				+ ISNULL(CAST(@errnum AS NVARCHAR(10)), N'any number') + N' ' + ISNULL(@msg_like, N'(any message)')
			END;

	IF @state = -1
		SET @detail = ISNULL(@detail + NCHAR(10), N'') + N'The error doomed the transaction (XACT_STATE() = -1): '
			+ N'nothing after this assertion can run. Use isolation=snapshot for code that dooms transactions.';

	EXEC tap._emit @ok, @description, @detail;

	RETURN;
END CATCH;

EXEC tap._emit 0, @description, N'no error was raised';
GO

CREATE OR ALTER PROCEDURE tap.lives_ok
	@sql NVARCHAR(MAX)
	,@description NVARCHAR(4000) = NULL
AS
SET NOCOUNT ON;
SET XACT_ABORT OFF;

DECLARE @trancount INT = @@TRANCOUNT;

BEGIN TRY
	EXEC sys.sp_executesql @sql;
END TRY
BEGIN CATCH
	-- As in tap.throws_ok: a transaction the code opened and the caught error left open is rolled back.
	IF @trancount = 0 AND @@TRANCOUNT > 0
		ROLLBACK TRANSACTION;

	DECLARE @detail NVARCHAR(MAX) = N'raised: ' + CAST(ERROR_NUMBER() AS NVARCHAR(10)) + N' ' + ERROR_MESSAGE();

	EXEC tap._emit 0, @description, @detail;

	RETURN;
END CATCH;

EXEC tap._emit 1, @description;
GO
