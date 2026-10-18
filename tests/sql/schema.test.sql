/* Schema and permission assertions, all passing. */
EXEC tap.has_schema N'dbo';
EXEC tap.has_table N'dbo.Trust';
EXEC tap.hasnt_table N'dbo.NoSuchTable';
EXEC tap.has_view N'dbo.vw_ActiveTrust';
EXEC tap.has_proc N'dbo.usp_AddTrust';
EXEC tap.has_function N'dbo.fn_TrustLabel';
EXEC tap.has_trigger N'dbo.Trust', N'TR_Trust_Audit';

EXEC tap.has_column N'dbo.Trust', N'Score';
EXEC tap.hasnt_column N'dbo.Trust', N'Nope';
EXEC tap.col_type_is N'dbo.Trust', N'Score', N'decimal(5,2)';
EXEC tap.col_type_is N'dbo.Trust', N'Name', N'nvarchar(100)';
EXEC tap.col_type_is N'dbo.Trust', N'CreatedAt', N'datetime2(0)';
EXEC tap.col_not_null N'dbo.Trust', N'Name';
EXEC tap.col_is_null N'dbo.Trust', N'Score';
EXEC tap.col_has_default N'dbo.Trust', N'IsActive';
EXEC tap.col_is_identity N'dbo.Trust', N'TrustId';

EXEC tap.has_pk N'dbo.Trust';
EXEC tap.col_is_pk N'dbo.Region', N'RegionCode';
EXEC tap.has_fk N'dbo.Trust', N'dbo.Region';
EXEC tap.has_index N'dbo.Trust', N'IX_Trust_RegionCode';
EXEC tap.has_check N'dbo.Trust', N'CK_Trust_Score';

EXEC tap.has_permission N'ReaderUser', N'dbo.Region', N'SELECT';
EXEC tap.hasnt_permission N'ReaderUser', N'dbo.Trust', N'SELECT';
EXEC tap.has_permission N'ReportingRole', N'dbo.Trust', N'SELECT', N'role: schema-level grant';
EXEC tap.hasnt_permission N'ReportingRole', N'dbo.TrustAudit', N'SELECT', N'role: DENY beats the schema grant';
