/* Fixture schema for tsqltap's own tests: a little of everything the assertions need to see. */
CREATE TABLE dbo.Region (
	RegionCode VARCHAR(10) NOT NULL CONSTRAINT PK_Region PRIMARY KEY
	,Name NVARCHAR(100) NOT NULL
	);

CREATE TABLE dbo.Trust (
	TrustId INT IDENTITY(1, 1) NOT NULL CONSTRAINT PK_Trust PRIMARY KEY
	,TrustCode VARCHAR(10) NOT NULL CONSTRAINT UQ_Trust_TrustCode UNIQUE
	,RegionCode VARCHAR(10) NOT NULL CONSTRAINT FK_Trust_Region REFERENCES dbo.Region (RegionCode)
	,Name NVARCHAR(100) NOT NULL
	,Score DECIMAL(5, 2) NULL CONSTRAINT CK_Trust_Score CHECK (Score BETWEEN 0 AND 100)
	,IsActive BIT NOT NULL CONSTRAINT DF_Trust_IsActive DEFAULT (1)
	,OpenedOn DATE NOT NULL
	,CreatedAt DATETIME2(0) NOT NULL CONSTRAINT DF_Trust_CreatedAt DEFAULT (SYSUTCDATETIME())
	);

CREATE INDEX IX_Trust_RegionCode ON dbo.Trust (RegionCode);

CREATE TABLE dbo.TrustAudit (
	AuditId INT IDENTITY(1, 1) NOT NULL CONSTRAINT PK_TrustAudit PRIMARY KEY
	,TrustCode VARCHAR(10) NOT NULL
	,Action VARCHAR(10) NOT NULL
	);

CREATE TABLE dbo.Notification (
	NotificationId INT IDENTITY(1, 1) NOT NULL PRIMARY KEY
	,Message NVARCHAR(200) NOT NULL
	,Level INT NOT NULL
	);
GO

CREATE TRIGGER dbo.TR_Trust_Audit ON dbo.Trust
AFTER INSERT
AS
SET NOCOUNT ON;

INSERT dbo.TrustAudit (TrustCode, Action)
SELECT TrustCode, 'insert'
FROM inserted;
GO

CREATE VIEW dbo.vw_ActiveTrust
AS
SELECT t.TrustCode, t.Name, r.Name AS RegionName
FROM dbo.Trust t
JOIN dbo.Region r ON r.RegionCode = t.RegionCode
WHERE t.IsActive = 1;
GO

CREATE PROCEDURE dbo.usp_Notify
	@Message NVARCHAR(200)
	,@Level INT = 1
AS
SET NOCOUNT ON;

INSERT dbo.Notification (Message, Level) VALUES (@Message, @Level);
GO

CREATE PROCEDURE dbo.usp_AddTrust
	@TrustCode VARCHAR(10)
	,@RegionCode VARCHAR(10)
	,@Name NVARCHAR(100)
AS
SET NOCOUNT ON;

INSERT dbo.Trust (TrustCode, RegionCode, Name, OpenedOn)
VALUES (@TrustCode, @RegionCode, @Name, CAST(SYSUTCDATETIME() AS DATE));

DECLARE @msg NVARCHAR(200) = N'added ' + @TrustCode;

EXEC dbo.usp_Notify @Message = @msg;
GO

CREATE PROCEDURE dbo.usp_TrustsInRegion
	@RegionCode VARCHAR(10)
AS
SET NOCOUNT ON;

SELECT TrustCode, Name
FROM dbo.Trust
WHERE RegionCode = @RegionCode
ORDER BY TrustCode;
GO

-- Commits its own transaction -- or, when @Name = 'bad', rolls it back, which also ends any
-- transaction it was called inside (ROLLBACK always goes to the outermost BEGIN TRANSACTION).
CREATE PROCEDURE dbo.usp_AddRegionOrRollBack
	@RegionCode VARCHAR(10)
	,@Name NVARCHAR(100)
AS
SET NOCOUNT ON;

BEGIN TRANSACTION;

INSERT dbo.Region (RegionCode, Name) VALUES (@RegionCode, @Name);

IF @Name = N'bad'
BEGIN
	ROLLBACK TRANSACTION;

	RETURN;
END;

COMMIT TRANSACTION;
GO

CREATE FUNCTION dbo.fn_TrustLabel (@TrustCode VARCHAR(10), @Name NVARCHAR(100))
RETURNS NVARCHAR(120)
AS
BEGIN
	RETURN @TrustCode + N' - ' + @Name;
END;
GO

-- Principals for the permission assertions.
CREATE USER ReaderUser WITHOUT LOGIN;
CREATE ROLE ReportingRole;

GRANT SELECT ON dbo.Region TO ReaderUser;
GRANT SELECT ON SCHEMA::dbo TO ReportingRole;
DENY SELECT ON dbo.TrustAudit TO ReportingRole;
GO

-- Opens its own transaction and raises under XACT_ABORT ON, leaving the transaction open when the
-- error is caught -- what tap.throws_ok has to clean up after.
CREATE PROCEDURE dbo.usp_OpensTransactionThenThrows
AS
SET XACT_ABORT ON;

BEGIN TRANSACTION;

INSERT dbo.Region (RegionCode, Name) VALUES ('X1', N'Never');

THROW 50999, 'thrown inside its own transaction', 1;
GO

-- Catches its own error under XACT_ABORT ON: the caller's transaction is left doomed (XACT_STATE() = -1).
CREATE PROCEDURE dbo.usp_DoomsTransaction
AS
SET XACT_ABORT ON;

BEGIN TRY
	THROW 50998, 'dooms the caller''s transaction', 1;
END TRY
BEGIN CATCH
END CATCH;
GO
