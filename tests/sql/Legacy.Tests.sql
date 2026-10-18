/*
A script in the older hand-rolled shape (the *.Tests.sql files):
it manages its own transaction, returns (Seq, Check, Result, Detail) rows and THROWs if any failed.
Files named *.Tests.sql run with isolation=none so their own ROLLBACK is not mistaken for a leak.
*/
SET NOCOUNT ON;

DECLARE @Results TABLE (Seq INT IDENTITY, Check_ VARCHAR(90), Result VARCHAR(4), Detail VARCHAR(400));

BEGIN TRANSACTION;

INSERT dbo.Region (RegionCode, Name) VALUES ('L1', N'Legacy');

INSERT @Results (Check_, Result, Detail)
SELECT 'region inserted', CASE WHEN COUNT(*) = 1 THEN 'PASS' ELSE 'FAIL' END, NULL
FROM dbo.Region
WHERE RegionCode = 'L1';

INSERT @Results (Check_, Result, Detail)
SELECT 'baseline regions present', CASE WHEN COUNT(*) = 2 THEN 'PASS' ELSE 'FAIL' END, CONCAT(COUNT(*), ' baseline rows')
FROM dbo.Region
WHERE RegionCode IN ('N1', 'S1');

ROLLBACK TRANSACTION;

SELECT Seq, Check_ AS [Check], Result, Detail FROM @Results ORDER BY Seq;

IF EXISTS (SELECT 1 FROM @Results WHERE Result <> 'PASS')
	THROW 50000, 'one or more checks failed', 1;
