/*
Schema assertions. Object names are anything OBJECT_ID accepts: 'App.Notification', '[App].[Notification]'.
Each takes an optional @description; the default says what was checked.

(T-SQL only accepts variables and constants as EXEC arguments, hence the DECLARE-then-EXEC shape.)
*/
CREATE OR ALTER PROCEDURE tap._has_object
	@name NVARCHAR(400)
	,@types NVARCHAR(100) -- comma-separated sys.objects.type codes
	,@what NVARCHAR(50)
	,@want_exists BIT
	,@description NVARCHAR(4000) = NULL
AS
SET NOCOUNT ON;

DECLARE @exists BIT = CASE
		WHEN EXISTS (
				SELECT 1
				FROM sys.objects
				WHERE object_id = OBJECT_ID(@name)
					AND RTRIM(type) IN (SELECT TRIM(value) FROM STRING_SPLIT(@types, N','))
				)
			THEN 1
		ELSE 0
		END;
DECLARE @ok BIT = CASE WHEN @exists = @want_exists THEN 1 ELSE 0 END;

SET @description = ISNULL(@description, @what + N' ' + @name + CASE WHEN @want_exists = 1 THEN N' should exist' ELSE N' should not exist' END);

EXEC tap._emit @ok, @description;
GO

CREATE OR ALTER PROCEDURE tap.has_table @table NVARCHAR(400), @description NVARCHAR(4000) = NULL
AS EXEC tap._has_object @table, N'U', N'Table', 1, @description;
GO
CREATE OR ALTER PROCEDURE tap.hasnt_table @table NVARCHAR(400), @description NVARCHAR(4000) = NULL
AS EXEC tap._has_object @table, N'U', N'Table', 0, @description;
GO
CREATE OR ALTER PROCEDURE tap.has_view @view NVARCHAR(400), @description NVARCHAR(4000) = NULL
AS EXEC tap._has_object @view, N'V', N'View', 1, @description;
GO
CREATE OR ALTER PROCEDURE tap.hasnt_view @view NVARCHAR(400), @description NVARCHAR(4000) = NULL
AS EXEC tap._has_object @view, N'V', N'View', 0, @description;
GO
CREATE OR ALTER PROCEDURE tap.has_proc @procedure NVARCHAR(400), @description NVARCHAR(4000) = NULL
AS EXEC tap._has_object @procedure, N'P,PC', N'Procedure', 1, @description;
GO
CREATE OR ALTER PROCEDURE tap.hasnt_proc @procedure NVARCHAR(400), @description NVARCHAR(4000) = NULL
AS EXEC tap._has_object @procedure, N'P,PC', N'Procedure', 0, @description;
GO
CREATE OR ALTER PROCEDURE tap.has_function @function NVARCHAR(400), @description NVARCHAR(4000) = NULL
AS EXEC tap._has_object @function, N'FN,IF,TF,FS,FT', N'Function', 1, @description;
GO
CREATE OR ALTER PROCEDURE tap.hasnt_function @function NVARCHAR(400), @description NVARCHAR(4000) = NULL
AS EXEC tap._has_object @function, N'FN,IF,TF,FS,FT', N'Function', 0, @description;
GO

CREATE OR ALTER PROCEDURE tap.has_schema
	@schema SYSNAME
	,@description NVARCHAR(4000) = NULL
AS
DECLARE @ok BIT = CASE WHEN SCHEMA_ID(@schema) IS NOT NULL THEN 1 ELSE 0 END;

SET @description = ISNULL(@description, N'Schema ' + @schema + N' should exist');

EXEC tap._emit @ok, @description;
GO

CREATE OR ALTER PROCEDURE tap.has_trigger
	@table NVARCHAR(400)
	,@trigger SYSNAME
	,@description NVARCHAR(4000) = NULL
AS
DECLARE @ok BIT = CASE
		WHEN EXISTS (SELECT 1 FROM sys.triggers WHERE parent_id = OBJECT_ID(@table) AND name = @trigger) THEN 1
		ELSE 0
		END;

SET @description = ISNULL(@description, N'Table ' + @table + N' should have trigger ' + @trigger);

EXEC tap._emit @ok, @description;
GO

/* ------------------------------------------------------------------ columns */

CREATE OR ALTER PROCEDURE tap.has_column
	@table NVARCHAR(400)
	,@column SYSNAME
	,@description NVARCHAR(4000) = NULL
AS
DECLARE @ok BIT = CASE WHEN COL_LENGTH(@table, @column) IS NOT NULL THEN 1 ELSE 0 END;

SET @description = ISNULL(@description, N'Column ' + @table + N'.' + @column + N' should exist');

EXEC tap._emit @ok, @description;
GO

CREATE OR ALTER PROCEDURE tap.hasnt_column
	@table NVARCHAR(400)
	,@column SYSNAME
	,@description NVARCHAR(4000) = NULL
AS
DECLARE @ok BIT = CASE WHEN COL_LENGTH(@table, @column) IS NULL THEN 1 ELSE 0 END;

SET @description = ISNULL(@description, N'Column ' + @table + N'.' + @column + N' should not exist');

EXEC tap._emit @ok, @description;
GO

-- @type as you would write it in DDL: 'int', 'nvarchar(200)', 'decimal(10,2)', 'varchar(max)', 'datetime2(0)'.
CREATE OR ALTER PROCEDURE tap.col_type_is
	@table NVARCHAR(400)
	,@column SYSNAME
	,@type NVARCHAR(200)
	,@description NVARCHAR(4000) = NULL
AS
SET NOCOUNT ON;

DECLARE @have NVARCHAR(200) = (
		SELECT tap.format_type(TYPE_NAME(c.user_type_id), c.max_length, c.precision, c.scale)
		FROM sys.columns c
		WHERE c.object_id = OBJECT_ID(@table)
			AND c.name = @column
		);
DECLARE @ok BIT = CASE WHEN LOWER(REPLACE(@have, N' ', N'')) = LOWER(REPLACE(@type, N' ', N'')) THEN 1 ELSE 0 END;
DECLARE @detail NVARCHAR(MAX) = CASE WHEN @ok = 0 THEN N'have: ' + ISNULL(@have, N'(no such column)') + NCHAR(10) + N'want: ' + @type END;

SET @description = ISNULL(@description, N'Column ' + @table + N'.' + @column + N' should be type ' + @type);

EXEC tap._emit @ok, @description, @detail;
GO

CREATE OR ALTER PROCEDURE tap._col_flag
	@table NVARCHAR(400)
	,@column SYSNAME
	,@flag VARCHAR(20) -- nullable | identity | default
	,@want BIT
	,@should NVARCHAR(100)
	,@description NVARCHAR(4000)
AS
SET NOCOUNT ON;

DECLARE @have BIT = (
		SELECT CASE @flag
				WHEN 'nullable' THEN c.is_nullable
				WHEN 'identity' THEN c.is_identity
				WHEN 'default' THEN CASE WHEN c.default_object_id <> 0 THEN 1 ELSE 0 END
				END
		FROM sys.columns c
		WHERE c.object_id = OBJECT_ID(@table)
			AND c.name = @column
		);
DECLARE @ok BIT = CASE WHEN @have = @want THEN 1 ELSE 0 END;
DECLARE @detail NVARCHAR(MAX) = CASE WHEN @have IS NULL THEN N'no such column' END;

SET @description = ISNULL(@description, N'Column ' + @table + N'.' + @column + N' should ' + @should);

EXEC tap._emit @ok, @description, @detail;
GO

CREATE OR ALTER PROCEDURE tap.col_not_null @table NVARCHAR(400), @column SYSNAME, @description NVARCHAR(4000) = NULL
AS EXEC tap._col_flag @table, @column, 'nullable', 0, N'be NOT NULL', @description;
GO
CREATE OR ALTER PROCEDURE tap.col_is_null @table NVARCHAR(400), @column SYSNAME, @description NVARCHAR(4000) = NULL
AS EXEC tap._col_flag @table, @column, 'nullable', 1, N'allow NULL', @description;
GO
CREATE OR ALTER PROCEDURE tap.col_has_default @table NVARCHAR(400), @column SYSNAME, @description NVARCHAR(4000) = NULL
AS EXEC tap._col_flag @table, @column, 'default', 1, N'have a default', @description;
GO
CREATE OR ALTER PROCEDURE tap.col_is_identity @table NVARCHAR(400), @column SYSNAME, @description NVARCHAR(4000) = NULL
AS EXEC tap._col_flag @table, @column, 'identity', 1, N'be an IDENTITY', @description;
GO

/* ------------------------------------------------------------------ keys, indexes, constraints */

CREATE OR ALTER PROCEDURE tap.has_pk
	@table NVARCHAR(400)
	,@description NVARCHAR(4000) = NULL
AS
DECLARE @ok BIT = CASE WHEN EXISTS (SELECT 1 FROM sys.key_constraints WHERE parent_object_id = OBJECT_ID(@table) AND type = 'PK') THEN 1 ELSE 0 END;

SET @description = ISNULL(@description, N'Table ' + @table + N' should have a primary key');

EXEC tap._emit @ok, @description;
GO

-- @columns: comma-separated, in key order.
CREATE OR ALTER PROCEDURE tap.col_is_pk
	@table NVARCHAR(400)
	,@columns NVARCHAR(4000)
	,@description NVARCHAR(4000) = NULL
AS
SET NOCOUNT ON;

DECLARE @have NVARCHAR(4000) = (
		SELECT STRING_AGG(c.name, N',') WITHIN GROUP (ORDER BY ic.key_ordinal)
		FROM sys.key_constraints kc
		JOIN sys.index_columns ic ON ic.object_id = kc.parent_object_id AND ic.index_id = kc.unique_index_id
		JOIN sys.columns c ON c.object_id = ic.object_id AND c.column_id = ic.column_id
		WHERE kc.parent_object_id = OBJECT_ID(@table)
			AND kc.type = 'PK'
			AND ic.key_ordinal > 0
		);
DECLARE @ok BIT = CASE WHEN LOWER(@have) = LOWER(REPLACE(@columns, N' ', N'')) THEN 1 ELSE 0 END;
DECLARE @detail NVARCHAR(MAX) = CASE WHEN @ok = 0 THEN N'have: ' + ISNULL(N'(' + @have + N')', N'(no primary key)') END;

SET @description = ISNULL(@description, N'Primary key of ' + @table + N' should be (' + @columns + N')');

EXEC tap._emit @ok, @description, @detail;
GO

CREATE OR ALTER PROCEDURE tap.has_fk
	@table NVARCHAR(400)
	,@references NVARCHAR(400) = NULL
	,@description NVARCHAR(4000) = NULL
AS
DECLARE @ok BIT = CASE
		WHEN EXISTS (
				SELECT 1
				FROM sys.foreign_keys
				WHERE parent_object_id = OBJECT_ID(@table)
					AND (@references IS NULL OR referenced_object_id = OBJECT_ID(@references))
				)
			THEN 1
		ELSE 0
		END;

SET @description = ISNULL(@description, N'Table ' + @table + N' should have a foreign key' + ISNULL(N' to ' + @references, N''));

EXEC tap._emit @ok, @description;
GO

CREATE OR ALTER PROCEDURE tap.has_index
	@table NVARCHAR(400)
	,@index SYSNAME
	,@description NVARCHAR(4000) = NULL
AS
DECLARE @ok BIT = CASE WHEN EXISTS (SELECT 1 FROM sys.indexes WHERE object_id = OBJECT_ID(@table) AND name = @index) THEN 1 ELSE 0 END;

SET @description = ISNULL(@description, N'Table ' + @table + N' should have index ' + @index);

EXEC tap._emit @ok, @description;
GO

CREATE OR ALTER PROCEDURE tap.has_check
	@table NVARCHAR(400)
	,@constraint SYSNAME = NULL
	,@description NVARCHAR(4000) = NULL
AS
DECLARE @ok BIT = CASE
		WHEN EXISTS (
				SELECT 1
				FROM sys.check_constraints
				WHERE parent_object_id = OBJECT_ID(@table)
					AND (@constraint IS NULL OR name = @constraint)
				)
			THEN 1
		ELSE 0
		END;

SET @description = ISNULL(@description, N'Table ' + @table + N' should have check constraint' + ISNULL(N' ' + @constraint, N''));

EXEC tap._emit @ok, @description;
GO

/* ------------------------------------------------------------------ permissions */

/*
Does @principal (a database user or role) hold @permission on @securable, as SQL Server evaluates
it -- DENY, role membership and all? @securable NULL means the database itself.

    EXEC tap.has_permission 'ApiUser', 'App.Notification', 'SELECT';
    EXEC tap.hasnt_permission 'ApiUser', 'App.AuditLog', 'SELECT';

For a role, a throwaway WITHOUT LOGIN user is added to it and checked instead (roles cannot be
impersonated); it is dropped again before returning.
*/
CREATE OR ALTER PROCEDURE tap._permission
	@principal SYSNAME
	,@securable NVARCHAR(400)
	,@permission NVARCHAR(60)
	,@want BIT
	,@description NVARCHAR(4000)
AS
SET NOCOUNT ON;

DECLARE @detail NVARCHAR(MAX);

SET @description = ISNULL(@description, @principal + CASE WHEN @want = 1 THEN N' should have ' ELSE N' should not have ' END
		+ @permission + N' on ' + ISNULL(@securable, N'the database'));

IF DATABASE_PRINCIPAL_ID(@principal) IS NULL
BEGIN
	SET @detail = N'no such database principal';

	EXEC tap._emit 0, @description, @detail;

	RETURN;
END;

DECLARE @have INT;
DECLARE @class NVARCHAR(60) = CASE WHEN @securable IS NULL THEN N'DATABASE' ELSE N'OBJECT' END;
DECLARE @as SYSNAME = @principal;
DECLARE @sql NVARCHAR(MAX);

IF EXISTS (SELECT 1 FROM sys.database_principals WHERE name = @principal AND type = 'R')
BEGIN
	SET @as = N'tap_probe_' + REPLACE(CONVERT(NVARCHAR(36), NEWID()), N'-', N'');
	SET @sql = N'CREATE USER ' + QUOTENAME(@as) + N' WITHOUT LOGIN; ALTER ROLE ' + QUOTENAME(@principal) + N' ADD MEMBER ' + QUOTENAME(@as) + N';';

	EXEC sys.sp_executesql @sql;
END;

EXECUTE AS USER = @as;

SET @have = HAS_PERMS_BY_NAME(@securable, @class, @permission);

REVERT;

IF @as <> @principal
BEGIN
	SET @sql = N'ALTER ROLE ' + QUOTENAME(@principal) + N' DROP MEMBER ' + QUOTENAME(@as) + N'; DROP USER ' + QUOTENAME(@as) + N';';

	EXEC sys.sp_executesql @sql;
END;

DECLARE @ok BIT = CASE WHEN ISNULL(@have, 0) = @want THEN 1 ELSE 0 END;

SET @detail = CASE WHEN @have IS NULL THEN N'HAS_PERMS_BY_NAME returned NULL: unknown securable or permission' END;

EXEC tap._emit @ok, @description, @detail;
GO

CREATE OR ALTER PROCEDURE tap.has_permission
	@principal SYSNAME
	,@securable NVARCHAR(400)
	,@permission NVARCHAR(60)
	,@description NVARCHAR(4000) = NULL
AS
EXEC tap._permission @principal, @securable, @permission, 1, @description;
GO

CREATE OR ALTER PROCEDURE tap.hasnt_permission
	@principal SYSNAME
	,@securable NVARCHAR(400)
	,@permission NVARCHAR(60)
	,@description NVARCHAR(4000) = NULL
AS
EXEC tap._permission @principal, @securable, @permission, 0, @description;
GO
