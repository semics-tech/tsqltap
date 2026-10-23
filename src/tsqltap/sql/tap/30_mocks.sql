/*
Mocking: fake_table and spy_procedure. Both rename the real object out of the way and put a
stand-in under its name. They are DDL, which SQL Server rolls back with the transaction, so both
REQUIRE an open transaction (the default isolation=transaction gives one) and refuse to run without.
*/

/*
Swap @table for an EMPTY copy with the same columns, under the same name.

Unlike tSQLt.FakeTable, constraints and triggers are KEPT by default -- the point is usually an
empty table, not a table without its rules. Foreign keys are the exception (@keep_fks = 0): the
common reason to fake a table is to avoid seeding its parents. Re-created constraints and triggers
keep their original names, so error messages read the same.

Options (all default to 1, i.e. keep the original behaviour, except @keep_fks):
  @keep_not_null = 0  every column becomes NULLable, so a test can INSERT just the columns it cares
                      about. Columns of a kept PRIMARY KEY stay NOT NULL (SQL Server requires it)
                      unless @keep_keys = 0; rowversion columns and a kept IDENTITY column (@keep_identity = 1) always
                      stay NOT NULL.
  @keep_identity = 0  the copy has no IDENTITY property, so tests can insert explicit ids without
                      SET IDENTITY_INSERT.
  @tsqlt_compatible = 1  shorthand: sets @keep_defaults, @keep_checks, @keep_keys, @keep_fks,
                      @keep_triggers, @keep_not_null and @keep_identity to 0, like tSQLt.FakeTable.
                      It overrides those arguments. Passing it as 0 changes nothing.

Differences from tSQLt.FakeTable: by default tap.fake_table keeps NOT NULL, IDENTITY and the
constraints and triggers (see above), while tSQLt drops all of them (and its @Identity, @ComputedColumns
and @Defaults switches default to off). Use @tsqlt_compatible = 1 for tSQLt's behaviour; computed
columns are plain columns either way. Unlike tSQLt there is no switch to keep computed columns.

Limits: a table referenced by a SCHEMABINDING view cannot be renamed and the call fails. Column
types are re-stated for ALTER COLUMN from their base system type, so alias (user-defined) types
become their base type when @keep_not_null = 0.
*/
CREATE OR ALTER PROCEDURE tap.fake_table
	@table NVARCHAR(400)
	,@keep_defaults BIT = 1
	,@keep_checks BIT = 1
	,@keep_keys BIT = 1
	,@keep_fks BIT = 0
	,@keep_triggers BIT = 1
	,@keep_not_null BIT = 1
	,@keep_identity BIT = 1
	,@tsqlt_compatible BIT = 0
AS
SET NOCOUNT ON;

DECLARE @msg NVARCHAR(2048);

IF @tsqlt_compatible = 1
	SELECT @keep_defaults = 0
		,@keep_checks = 0
		,@keep_keys = 0
		,@keep_fks = 0
		,@keep_triggers = 0
		,@keep_not_null = 0
		,@keep_identity = 0;

IF @@TRANCOUNT = 0
	THROW 50001, N'tap.fake_table needs an open transaction so the swap is undone afterwards', 1;

DECLARE @id INT = OBJECT_ID(@table, N'U');

IF @id IS NULL
BEGIN
	SET @msg = N'tap.fake_table: no table ' + @table;

	THROW 50002, @msg, 1;
END;

DECLARE @schema SYSNAME = OBJECT_SCHEMA_NAME(@id)
	,@name SYSNAME = OBJECT_NAME(@id);
DECLARE @full NVARCHAR(600) = QUOTENAME(@schema) + N'.' + QUOTENAME(@name)
	,@moved SYSNAME = LEFT(@name, 110) + N'__tap_orig';
DECLARE @defs TABLE (
	seq INT IDENTITY PRIMARY KEY
	,ord TINYINT
	,obj_name SYSNAME
	,is_trigger BIT DEFAULT 0
	,ddl NVARCHAR(MAX)
	);

-- Capture every definition BEFORE anything is renamed.
IF @keep_keys = 1
	INSERT @defs (ord, obj_name, ddl)
	SELECT 1
		,kc.name
		,N'ALTER TABLE ' + @full + N' ADD CONSTRAINT ' + QUOTENAME(kc.name)
		+ CASE kc.type WHEN 'PK' THEN N' PRIMARY KEY ' ELSE N' UNIQUE ' END
		+ CASE i.type WHEN 1 THEN N'CLUSTERED' ELSE N'NONCLUSTERED' END + N' ('
		+ (
			SELECT STRING_AGG(QUOTENAME(c.name) + CASE ic.is_descending_key WHEN 1 THEN N' DESC' ELSE N'' END, N', ') WITHIN GROUP (ORDER BY ic.key_ordinal)
			FROM sys.index_columns ic
			JOIN sys.columns c ON c.object_id = ic.object_id AND c.column_id = ic.column_id
			WHERE ic.object_id = i.object_id AND ic.index_id = i.index_id AND ic.key_ordinal > 0
			) + N')'
	FROM sys.key_constraints kc
	JOIN sys.indexes i ON i.object_id = kc.parent_object_id AND i.index_id = kc.unique_index_id
	WHERE kc.parent_object_id = @id;

IF @keep_defaults = 1
	INSERT @defs (ord, obj_name, ddl)
	SELECT 2
		,dc.name
		,N'ALTER TABLE ' + @full + N' ADD CONSTRAINT ' + QUOTENAME(dc.name) + N' DEFAULT ' + dc.definition + N' FOR ' + QUOTENAME(c.name)
	FROM sys.default_constraints dc
	JOIN sys.columns c ON c.object_id = dc.parent_object_id AND c.column_id = dc.parent_column_id
	WHERE dc.parent_object_id = @id;

IF @keep_checks = 1
	INSERT @defs (ord, obj_name, ddl)
	SELECT 3
		,cc.name
		,N'ALTER TABLE ' + @full + N' ADD CONSTRAINT ' + QUOTENAME(cc.name) + N' CHECK ' + cc.definition
	FROM sys.check_constraints cc
	WHERE cc.parent_object_id = @id
		AND cc.is_disabled = 0;

IF @keep_fks = 1
	INSERT @defs (ord, obj_name, ddl)
	SELECT 4
		,fk.name
		,N'ALTER TABLE ' + @full + N' ADD CONSTRAINT ' + QUOTENAME(fk.name) + N' FOREIGN KEY ('
		+ (
			SELECT STRING_AGG(QUOTENAME(COL_NAME(fkc.parent_object_id, fkc.parent_column_id)), N', ') WITHIN GROUP (ORDER BY fkc.constraint_column_id)
			FROM sys.foreign_key_columns fkc
			WHERE fkc.constraint_object_id = fk.object_id
			) + N') REFERENCES ' + QUOTENAME(OBJECT_SCHEMA_NAME(fk.referenced_object_id)) + N'.' + QUOTENAME(OBJECT_NAME(fk.referenced_object_id)) + N' ('
		+ (
			SELECT STRING_AGG(QUOTENAME(COL_NAME(fkc.referenced_object_id, fkc.referenced_column_id)), N', ') WITHIN GROUP (ORDER BY fkc.constraint_column_id)
			FROM sys.foreign_key_columns fkc
			WHERE fkc.constraint_object_id = fk.object_id
			) + N')'
	FROM sys.foreign_keys fk
	WHERE fk.parent_object_id = @id;

IF @keep_triggers = 1
	INSERT @defs (ord, obj_name, is_trigger, ddl)
	SELECT 5
		,tr.name
		,1
		,OBJECT_DEFINITION(tr.object_id)
	FROM sys.triggers tr
	WHERE tr.parent_id = @id;

-- Move the originals out of the way: constraint and trigger names are schema-scoped, so they have
-- to be renamed before the copies can take them. Foreign keys are renamed whether kept or not.
DECLARE @obj SYSNAME
	,@ddl NVARCHAR(MAX)
	,@old NVARCHAR(600)
	,@new SYSNAME;
DECLARE moves CURSOR LOCAL FAST_FORWARD
FOR
SELECT obj_name
FROM @defs

UNION

SELECT name
FROM sys.foreign_keys
WHERE parent_object_id = @id;

OPEN moves;

FETCH NEXT FROM moves INTO @obj;

WHILE @@FETCH_STATUS = 0
BEGIN
	SET @old = QUOTENAME(@schema) + N'.' + QUOTENAME(@obj);
	SET @new = LEFT(@obj, 110) + N'__tap_orig';

	EXEC sys.sp_rename @old, @new, N'OBJECT';

	FETCH NEXT FROM moves INTO @obj;
END;

CLOSE moves;
DEALLOCATE moves;

EXEC sys.sp_rename @full, @moved;

DECLARE @collist NVARCHAR(MAX) = N'*';

-- SELECT INTO keeps IDENTITY unless the column is part of an expression: ISNULL(c, c) drops the
-- property and, the column being NOT NULL, keeps the type and NOT NULL.
IF @keep_identity = 0
	AND EXISTS (SELECT 1 FROM sys.columns WHERE object_id = @id AND is_identity = 1)
	SELECT @collist = STRING_AGG(CONVERT(NVARCHAR(MAX), CASE
					WHEN c.is_identity = 1
						THEN N'ISNULL(' + QUOTENAME(c.name) + N', ' + QUOTENAME(c.name) + N') AS ' + QUOTENAME(c.name)
					ELSE QUOTENAME(c.name)
					END), N', ') WITHIN GROUP (ORDER BY c.column_id)
	FROM sys.columns c
	WHERE c.object_id = @id;

DECLARE @sql NVARCHAR(MAX) = N'SELECT TOP (0) ' + @collist + N' INTO ' + @full + N' FROM ' + QUOTENAME(@schema) + N'.' + QUOTENAME(@moved) + N';';

EXEC sys.sp_executesql @sql;

-- Make columns nullable before the constraints are re-created (a default must not be on the column
-- yet). A kept PK keeps its columns NOT NULL; rowversion and a kept IDENTITY column stay NOT NULL.
IF @keep_not_null = 0
BEGIN
	SET @sql = N'';

	SELECT @sql += N'ALTER TABLE ' + @full + N' ALTER COLUMN ' + QUOTENAME(c.name) + N' '
		+ tap.format_type(TYPE_NAME(c.system_type_id), c.max_length, c.precision, c.scale)
		+ CASE
			WHEN c.collation_name IS NOT NULL
				THEN N' COLLATE ' + c.collation_name
			ELSE N''
			END + N' NULL;' + NCHAR(10)
	FROM sys.columns c
	WHERE c.object_id = @id
		AND c.is_nullable = 0
		AND c.is_computed = 0
		AND NOT (c.is_identity = 1 AND @keep_identity = 1) -- an identity column cannot be NULLable
		AND TYPE_NAME(c.system_type_id) NOT IN (N'timestamp', N'rowversion')
		AND NOT (
			@keep_keys = 1
			AND EXISTS (
				SELECT 1
				FROM sys.key_constraints kc
				JOIN sys.index_columns ic ON ic.object_id = kc.parent_object_id AND ic.index_id = kc.unique_index_id
				WHERE kc.parent_object_id = @id
					AND kc.type = 'PK'
					AND ic.column_id = c.column_id
				)
			)
	ORDER BY c.column_id;

	IF @sql <> N''
		EXEC sys.sp_executesql @sql;
END;

DECLARE @is_trigger BIT;
DECLARE recreate CURSOR LOCAL FAST_FORWARD
FOR
SELECT ddl
	,is_trigger
FROM @defs
ORDER BY ord
	,seq;

OPEN recreate;

FETCH NEXT FROM recreate INTO @ddl, @is_trigger;

WHILE @@FETCH_STATUS = 0
BEGIN
	-- A trigger body must be the only statement in its batch: sp_executesql gives it one.
	EXEC sys.sp_executesql @ddl;

	FETCH NEXT FROM recreate INTO @ddl, @is_trigger;
END;

CLOSE recreate;
DEALLOCATE recreate;
GO

/* ------------------------------------------------------------------ spy_procedure */

CREATE OR ALTER FUNCTION tap.spy_log_name (@procedure NVARCHAR(400))
RETURNS NVARCHAR(400)
AS
BEGIN
	RETURN N'tap.' + QUOTENAME(N'SpyLog_' + OBJECT_SCHEMA_NAME(OBJECT_ID(@procedure)) + N'_' + OBJECT_NAME(OBJECT_ID(@procedure)));
END;
GO

/*
Replace @procedure with a spy that records each call's parameters in tap.[SpyLog_<schema>_<name>]
and then runs @command (if any). Every parameter of the spy defaults to NULL, so callers relying on
the real procedure's defaults still work. Table-valued parameters are accepted but not logged.

    EXEC tap.spy_procedure 'App.usp_SendEmail';
    EXEC App.usp_DoTheThing;                                     -- calls usp_SendEmail
    EXEC tap.row_count_is 'SELECT * FROM tap.[SpyLog_App_usp_SendEmail]', 1;
*/
CREATE OR ALTER PROCEDURE tap.spy_procedure
	@procedure NVARCHAR(400)
	,@command NVARCHAR(MAX) = NULL
AS
SET NOCOUNT ON;

DECLARE @msg NVARCHAR(2048);

IF @@TRANCOUNT = 0
	THROW 50001, N'tap.spy_procedure needs an open transaction so the swap is undone afterwards', 1;

DECLARE @id INT = OBJECT_ID(@procedure, N'P');

IF @id IS NULL
BEGIN
	SET @msg = N'tap.spy_procedure: no procedure ' + @procedure;

	THROW 50002, @msg, 1;
END;

DECLARE @schema SYSNAME = OBJECT_SCHEMA_NAME(@id)
	,@name SYSNAME = OBJECT_NAME(@id);
DECLARE @full NVARCHAR(600) = QUOTENAME(@schema) + N'.' + QUOTENAME(@name)
	,@log NVARCHAR(400) = tap.spy_log_name(@procedure);
DECLARE @params TABLE (
	parameter_id INT PRIMARY KEY
	,pname SYSNAME
	,decl NVARCHAR(400)
	,logged BIT
	,is_output BIT
	);

INSERT @params (parameter_id, pname, decl, logged, is_output)
SELECT p.parameter_id
	,p.name
	,CASE
		WHEN t.is_table_type = 1 THEN QUOTENAME(SCHEMA_NAME(t.schema_id)) + N'.' + QUOTENAME(t.name) + N' READONLY'
		ELSE tap.format_type(TYPE_NAME(p.system_type_id), p.max_length, p.precision, p.scale)
		END
	,CASE WHEN t.is_table_type = 1 THEN 0 ELSE 1 END
	,p.is_output
FROM sys.parameters p
JOIN sys.types t ON t.user_type_id = p.user_type_id
WHERE p.object_id = @id
	AND p.parameter_id > 0;

DECLARE @sql NVARCHAR(MAX);

-- The log table.
IF OBJECT_ID(@log, N'U') IS NOT NULL
BEGIN
	SET @sql = N'DROP TABLE ' + @log + N';';

	EXEC sys.sp_executesql @sql;
END;

SET @sql = N'CREATE TABLE ' + @log + N' (_id_ INT IDENTITY(1, 1) PRIMARY KEY'
	+ ISNULL((
			SELECT N', ' + STRING_AGG(CONVERT(NVARCHAR(MAX), QUOTENAME(STUFF(pname, 1, 1, N'')) + N' ' + decl + N' NULL'), N', ') WITHIN GROUP (ORDER BY parameter_id)
			FROM @params
			WHERE logged = 1
			), N'') + N');';

EXEC sys.sp_executesql @sql;

-- Move the real procedure aside and create the spy under its name.
DECLARE @moved SYSNAME = LEFT(@name, 110) + N'__tap_orig';

EXEC sys.sp_rename @full, @moved;

DECLARE @signature NVARCHAR(MAX) = (
		SELECT STRING_AGG(CONVERT(NVARCHAR(MAX), pname + N' ' + decl + CASE WHEN logged = 1 THEN N' = NULL' ELSE N'' END + CASE WHEN is_output = 1 THEN N' OUTPUT' ELSE N'' END), N', ') WITHIN GROUP (ORDER BY parameter_id)
		FROM @params
		);
DECLARE @insert NVARCHAR(MAX) = N'INSERT ' + @log + ISNULL((
			SELECT N' (' + STRING_AGG(CONVERT(NVARCHAR(MAX), QUOTENAME(STUFF(pname, 1, 1, N''))), N', ') WITHIN GROUP (ORDER BY parameter_id)
				+ N') VALUES (' + STRING_AGG(CONVERT(NVARCHAR(MAX), pname), N', ') WITHIN GROUP (ORDER BY parameter_id) + N')'
			FROM @params
			WHERE logged = 1
			HAVING COUNT(*) > 0
			), N' DEFAULT VALUES') + N';';

SET @sql = N'CREATE PROCEDURE ' + @full + ISNULL(N' ' + @signature, N'') + N' AS BEGIN SET NOCOUNT ON; ' + @insert
	+ ISNULL(NCHAR(10) + @command + NCHAR(10), N'') + N' END;';

EXEC sys.sp_executesql @sql;
GO
