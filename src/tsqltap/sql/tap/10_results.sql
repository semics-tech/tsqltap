/*
Result-set comparisons: results_eq (ordered), bag_eq (any order, duplicates count), set_eq (any order,
duplicates ignored), is_empty, row_count_is.

@have is a query: a SELECT or an EXEC of a procedure. @want is a query or a bare VALUES list, e.g.

    EXEC tap.results_eq N'SELECT Id, Name FROM dbo.Thing ORDER BY Id', N'VALUES (1, N''a''), (2, N''b'')';

How: both sides are described with sys.dm_exec_describe_first_result_set and materialised into temp
tables, each in ITS OWN column types; the comparison converts the expected side to @have's types
with TRY_CONVERT. So VALUES ('2026-01-31') compares equal to a date column, and a value that cannot
convert ('abc' for an int column) is reported as a difference -- a conversion error would instead
doom the test's transaction. Strings compare under Latin1_General_BIN2 (exact, case-sensitive)
whatever the database collation is.

Limits: INSERT ... EXEC cannot nest, so @have cannot be a procedure that itself uses INSERT ... EXEC;
and a result set must be describable without running it (no temp tables created inside a procedure).
*/
CREATE OR ALTER PROCEDURE tap._describe
	@sql NVARCHAR(MAX)
	,@side NVARCHAR(40)
	,@error NVARCHAR(4000) OUTPUT
AS
SET NOCOUNT ON;

SET @error = (
		SELECT TOP (1) error_message
		FROM sys.dm_exec_describe_first_result_set(@sql, NULL, 0)
		WHERE error_message IS NOT NULL
		);

IF @error IS NOT NULL
	SET @error = N'could not determine the columns of ' + @side + N': ' + @error;
ELSE IF NOT EXISTS (SELECT 1 FROM sys.dm_exec_describe_first_result_set(@sql, NULL, 0))
	SET @error = @side + N' returns no result set';

SELECT column_ordinal AS ord
	,name
	,CASE
		WHEN system_type_name IN (N'timestamp', N'rowversion') THEN N'binary(8)'
		WHEN system_type_name IN (N'xml', N'ntext') THEN N'nvarchar(max)'
		WHEN system_type_name = N'text' THEN N'varchar(max)'
		WHEN system_type_name = N'image' THEN N'varbinary(max)'
		ELSE system_type_name
		END AS type
FROM sys.dm_exec_describe_first_result_set(@sql, NULL, 0)
WHERE is_hidden = 0
	AND @error IS NULL;
GO

CREATE OR ALTER PROCEDURE tap._compare
	@have NVARCHAR(MAX)
	,@want NVARCHAR(MAX) = NULL
	,@mode VARCHAR(10) -- ordered | bag | set | count
	,@want_count INT = NULL
	,@description NVARCHAR(4000) = NULL
AS
SET NOCOUNT ON;
SET XACT_ABORT OFF;

DECLARE @nl NCHAR(1) = NCHAR(10)
	,@err NVARCHAR(4000)
	,@sql NVARCHAR(MAX);
DECLARE @hcols TABLE (ord INT PRIMARY KEY, name SYSNAME NULL, type NVARCHAR(300) NOT NULL, display SYSNAME NULL);
DECLARE @wcols TABLE (ord INT PRIMARY KEY, name SYSNAME NULL, type NVARCHAR(300) NOT NULL);

INSERT @hcols (ord, name, type)
EXEC tap._describe @have, N'the query under test', @err OUTPUT;

IF @err IS NOT NULL
BEGIN
	EXEC tap._emit 0, @description, @err;

	RETURN;
END;

-- Display names: the real column name where it is present and unique, else cN.
UPDATE c
SET display = CASE
		WHEN c.name IS NOT NULL AND c.name <> N'' AND (SELECT COUNT(*) FROM @hcols d WHERE d.name = c.name) = 1 THEN c.name
		ELSE N'c' + CAST(c.ord AS NVARCHAR(10))
		END
FROM @hcols c;

DECLARE @collist NVARCHAR(MAX) = (
		SELECT STRING_AGG(CONVERT(NVARCHAR(MAX), N'c' + CAST(ord AS NVARCHAR(10))), N', ') WITHIN GROUP (ORDER BY ord)
		FROM @hcols
		);

-- The expected side, in its own types. A bare VALUES list is not a statement: give it column names.
DECLARE @want_q NVARCHAR(MAX) = @want;

IF @want IS NOT NULL
BEGIN
	IF TRIM(NCHAR(9) + NCHAR(10) + NCHAR(13) + N' ' FROM @want) LIKE N'VALUES%'
		SET @want_q = N'SELECT * FROM (' + @want + N') v (' + @collist + N')';

	INSERT @wcols (ord, name, type)
	EXEC tap._describe @want_q, N'the expected rows', @err OUTPUT;

	IF @err IS NULL AND (SELECT COUNT(*) FROM @wcols) <> (SELECT COUNT(*) FROM @hcols)
		SET @err = N'the query under test returns ' + CAST((SELECT COUNT(*) FROM @hcols) AS NVARCHAR(10))
			+ N' columns but the expected rows have ' + CAST((SELECT COUNT(*) FROM @wcols) AS NVARCHAR(10));

	IF @err IS NOT NULL
	BEGIN
		EXEC tap._emit 0, @description, @err;

		RETURN;
	END;
END;

DECLARE @bin2 NVARCHAR(40) = N' COLLATE Latin1_General_BIN2';
DECLARE @hdefs NVARCHAR(MAX)
	,@wdefs NVARCHAR(MAX)
	,@hdisplay NVARCHAR(MAX) -- cN AS [name], from #have
	,@wdisplay NVARCHAR(MAX) -- cN AS [name], from #want as written
	,@wconv NVARCHAR(MAX) -- TRY_CONVERT(have type, cN), from #want
	,@wconv_display NVARCHAR(MAX)
	,@hw NVARCHAR(MAX)
	,@ww NVARCHAR(MAX)
	,@unconvertible NVARCHAR(MAX);

SELECT @hdefs = STRING_AGG(CONVERT(NVARCHAR(MAX), c + N' ' + h.type + CASE WHEN h.type LIKE N'%char(%' THEN @bin2 ELSE N'' END + N' NULL'), N', ') WITHIN GROUP (ORDER BY h.ord)
	,@wdefs = STRING_AGG(CONVERT(NVARCHAR(MAX), c + N' ' + ISNULL(w.type, h.type) + CASE WHEN ISNULL(w.type, h.type) LIKE N'%char(%' THEN @bin2 ELSE N'' END + N' NULL'), N', ') WITHIN GROUP (ORDER BY h.ord)
	,@hdisplay = STRING_AGG(CONVERT(NVARCHAR(MAX), c + N' AS ' + QUOTENAME(h.display)), N', ') WITHIN GROUP (ORDER BY h.ord)
	,@wdisplay = STRING_AGG(CONVERT(NVARCHAR(MAX), c + N' AS ' + QUOTENAME(h.display)), N', ') WITHIN GROUP (ORDER BY h.ord)
	,@wconv = STRING_AGG(CONVERT(NVARCHAR(MAX), N'TRY_CONVERT(' + h.type + N', ' + c + N')' + CASE WHEN h.type LIKE N'%char(%' THEN @bin2 ELSE N'' END), N', ') WITHIN GROUP (ORDER BY h.ord)
	,@wconv_display = STRING_AGG(CONVERT(NVARCHAR(MAX), N'TRY_CONVERT(' + h.type + N', ' + c + N')' + CASE WHEN h.type LIKE N'%char(%' THEN @bin2 ELSE N'' END + N' AS ' + QUOTENAME(h.display)), N', ') WITHIN GROUP (ORDER BY h.ord)
	,@hw = STRING_AGG(CONVERT(NVARCHAR(MAX), N'h.' + c), N', ') WITHIN GROUP (ORDER BY h.ord)
	,@ww = STRING_AGG(CONVERT(NVARCHAR(MAX), N'TRY_CONVERT(' + h.type + N', w.' + c + N')' + CASE WHEN h.type LIKE N'%char(%' THEN @bin2 ELSE N'' END), N', ') WITHIN GROUP (ORDER BY h.ord)
	,@unconvertible = STRING_AGG(CONVERT(NVARCHAR(MAX), N'(' + c + N' IS NOT NULL AND TRY_CONVERT(' + h.type + N', ' + c + N') IS NULL)'), N' OR ') WITHIN GROUP (ORDER BY h.ord)
FROM @hcols h
LEFT JOIN @wcols w ON w.ord = h.ord
CROSS APPLY (SELECT N'c' + CAST(h.ord AS NVARCHAR(10)) AS c) n;

CREATE TABLE #have (__rn INT IDENTITY(1, 1) NOT NULL);
CREATE TABLE #want (__rn INT IDENTITY(1, 1) NOT NULL);

SET @sql = N'ALTER TABLE #have ADD ' + @hdefs + N'; ALTER TABLE #want ADD ' + @wdefs + N';';

EXEC sys.sp_executesql @sql;

DECLARE @savepoint BIT = 0;

IF @@TRANCOUNT > 0
BEGIN
	SAVE TRANSACTION tap_compare;
	SET @savepoint = 1;
END;

DECLARE @stage NVARCHAR(30) = N'the query under test';

BEGIN TRY
	SET @sql = N'INSERT #have (' + @collist + N') EXEC sys.sp_executesql @q;';

	EXEC sys.sp_executesql @sql, N'@q NVARCHAR(MAX)', @q = @have;

	IF @want IS NOT NULL
	BEGIN
		SET @stage = N'the expected rows';
		SET @sql = N'INSERT #want (' + @collist + N') EXEC sys.sp_executesql @q;';

		EXEC sys.sp_executesql @sql, N'@q NVARCHAR(MAX)', @q = @want_q;
	END;
END TRY
BEGIN CATCH
	DECLARE @msg NVARCHAR(4000) = N'error running ' + @stage + N': ' + CAST(ERROR_NUMBER() AS NVARCHAR(10)) + N' ' + ERROR_MESSAGE();

	IF XACT_STATE() = -1
		SET @msg += @nl + N'The error doomed the transaction (XACT_STATE() = -1): nothing after this assertion can run.';
	ELSE IF @savepoint = 1
		ROLLBACK TRANSACTION tap_compare;

	EXEC tap._emit 0, @description, @msg;

	RETURN;
END CATCH;

DECLARE @have_n INT = (SELECT COUNT(*) FROM #have)
	,@want_n INT = (SELECT COUNT(*) FROM #want)
	,@ok BIT
	,@detail NVARCHAR(MAX)
	,@j1 NVARCHAR(MAX)
	,@j2 NVARCHAR(MAX)
	,@n1 INT
	,@n2 INT
	,@rows NVARCHAR(100);

SET @rows = N'have ' + CAST(@have_n AS NVARCHAR(10)) + N' rows, want ' + CAST(ISNULL(@want_count, @want_n) AS NVARCHAR(10)) + N' rows';

IF @mode = 'count'
BEGIN
	SET @ok = CASE WHEN @have_n = @want_count THEN 1 ELSE 0 END;

	IF @ok = 0
	BEGIN
		SET @sql = N'SET @j = (SELECT TOP (5) ' + @hdisplay + N' FROM #have ORDER BY __rn FOR JSON PATH, INCLUDE_NULL_VALUES);';

		EXEC sys.sp_executesql @sql, N'@j NVARCHAR(MAX) OUTPUT', @j = @j1 OUTPUT;

		SET @detail = @rows + ISNULL(@nl + N'first rows: ' + @j1, N'');
	END;
END
ELSE IF @mode = 'ordered'
BEGIN
	DECLARE @rn INT;

	SET @sql = N'SELECT TOP (1) @rn = COALESCE(h.__rn, w.__rn) FROM #have h FULL JOIN #want w ON w.__rn = h.__rn'
		+ N' WHERE h.__rn IS NULL OR w.__rn IS NULL OR NOT EXISTS (SELECT ' + @hw + N' INTERSECT SELECT ' + @ww + N')'
		+ N' ORDER BY COALESCE(h.__rn, w.__rn);';

	EXEC sys.sp_executesql @sql, N'@rn INT OUTPUT', @rn = @rn OUTPUT;

	SET @ok = CASE WHEN @rn IS NULL THEN 1 ELSE 0 END;

	IF @ok = 0
	BEGIN
		SET @sql = N'SET @j1 = (SELECT ' + @hdisplay + N' FROM #have WHERE __rn = @rn FOR JSON PATH, WITHOUT_ARRAY_WRAPPER, INCLUDE_NULL_VALUES);'
			+ N'SET @j2 = (SELECT ' + @wdisplay + N' FROM #want WHERE __rn = @rn FOR JSON PATH, WITHOUT_ARRAY_WRAPPER, INCLUDE_NULL_VALUES);';

		EXEC sys.sp_executesql @sql, N'@rn INT, @j1 NVARCHAR(MAX) OUTPUT, @j2 NVARCHAR(MAX) OUTPUT', @rn = @rn, @j1 = @j1 OUTPUT, @j2 = @j2 OUTPUT;

		SET @detail = @rows + @nl
			+ N'first difference at row ' + CAST(@rn AS NVARCHAR(10)) + N':' + @nl
			+ N'  have: ' + ISNULL(@j1, N'(no row)') + @nl
			+ N'  want: ' + ISNULL(@j2, N'(no row)');
	END;
END
ELSE
BEGIN
	DECLARE @missing NVARCHAR(MAX)
		,@extra NVARCHAR(MAX);

	IF @mode = 'bag'
	BEGIN
		SET @missing = N'SELECT ' + @wconv_display + N', COUNT(*) AS [__count] FROM #want GROUP BY ' + @wconv
			+ N' EXCEPT SELECT ' + @collist + N', COUNT(*) FROM #have GROUP BY ' + @collist;
		SET @extra = N'SELECT ' + @hdisplay + N', COUNT(*) AS [__count] FROM #have GROUP BY ' + @collist
			+ N' EXCEPT SELECT ' + @wconv + N', COUNT(*) FROM #want GROUP BY ' + @wconv;
	END
	ELSE
	BEGIN
		SET @missing = N'SELECT ' + @wconv_display + N' FROM #want EXCEPT SELECT ' + @collist + N' FROM #have';
		SET @extra = N'SELECT ' + @hdisplay + N' FROM #have EXCEPT SELECT ' + @wconv + N' FROM #want';
	END;

	SET @sql = N'SELECT @n1 = COUNT(*) FROM (' + @missing + N') d;'
		+ N'SELECT @n2 = COUNT(*) FROM (' + @extra + N') d;'
		+ N'SET @j1 = (SELECT TOP (5) * FROM (' + @missing + N') d FOR JSON PATH, INCLUDE_NULL_VALUES);'
		+ N'SET @j2 = (SELECT TOP (5) * FROM (' + @extra + N') d FOR JSON PATH, INCLUDE_NULL_VALUES);';

	EXEC sys.sp_executesql @sql
		,N'@n1 INT OUTPUT, @n2 INT OUTPUT, @j1 NVARCHAR(MAX) OUTPUT, @j2 NVARCHAR(MAX) OUTPUT'
		,@n1 = @n1 OUTPUT
		,@n2 = @n2 OUTPUT
		,@j1 = @j1 OUTPUT
		,@j2 = @j2 OUTPUT;

	SET @ok = CASE WHEN @n1 = 0 AND @n2 = 0 THEN 1 ELSE 0 END;

	IF @ok = 0
		SET @detail = @rows
			+ CASE WHEN @n1 > 0 THEN @nl + N'missing (wanted, not returned): ' + @j1 ELSE N'' END
			+ CASE WHEN @n2 > 0 THEN @nl + N'extra (returned, not wanted): ' + @j2 ELSE N'' END;
END;

-- Say so when an expected value could not be converted to the column's type: it compared as NULL.
IF @ok = 0 AND @want IS NOT NULL
BEGIN
	DECLARE @bad NVARCHAR(MAX);

	SET @sql = N'SET @j = (SELECT TOP (3) ' + @wdisplay + N' FROM #want WHERE ' + @unconvertible + N' ORDER BY __rn FOR JSON PATH, INCLUDE_NULL_VALUES);';

	EXEC sys.sp_executesql @sql, N'@j NVARCHAR(MAX) OUTPUT', @j = @bad OUTPUT;

	IF @bad IS NOT NULL
		SET @detail += @nl + N'expected values that do not convert to the column types of the query under test: ' + @bad;
END;

EXEC tap._emit @ok, @description, @detail;
GO

CREATE OR ALTER PROCEDURE tap.results_eq
	@have NVARCHAR(MAX)
	,@want NVARCHAR(MAX)
	,@description NVARCHAR(4000) = NULL
AS
EXEC tap._compare @have = @have, @want = @want, @mode = 'ordered', @description = @description;
GO

CREATE OR ALTER PROCEDURE tap.bag_eq
	@have NVARCHAR(MAX)
	,@want NVARCHAR(MAX)
	,@description NVARCHAR(4000) = NULL
AS
EXEC tap._compare @have = @have, @want = @want, @mode = 'bag', @description = @description;
GO

CREATE OR ALTER PROCEDURE tap.set_eq
	@have NVARCHAR(MAX)
	,@want NVARCHAR(MAX)
	,@description NVARCHAR(4000) = NULL
AS
EXEC tap._compare @have = @have, @want = @want, @mode = 'set', @description = @description;
GO

CREATE OR ALTER PROCEDURE tap.is_empty
	@sql NVARCHAR(MAX)
	,@description NVARCHAR(4000) = NULL
AS
EXEC tap._compare @have = @sql, @mode = 'count', @want_count = 0, @description = @description;
GO

CREATE OR ALTER PROCEDURE tap.row_count_is
	@sql NVARCHAR(MAX)
	,@count INT
	,@description NVARCHAR(4000) = NULL
AS
EXEC tap._compare @have = @sql, @mode = 'count', @want_count = @count, @description = @description;
GO
