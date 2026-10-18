-- tsqltap: tsqlt=all; isolation=transaction
/*
The tSQLt bridge, against a stand-in tSQLt (created here, rolled back afterwards): after the file's
own batches, the runner calls tSQLt.RunAll and reports each row of tSQLt.TestResult as an assertion.
The real tSQLt.RunAll raises when a test fails; the stand-in raises too, to prove the runner reads
the results anyway. tests/test_failures.py covers a failing tSQLt case.
*/
EXEC (N'CREATE SCHEMA tSQLt');
GO

CREATE TABLE tSQLt.TestResult (Id INT IDENTITY, Class NVARCHAR(200), TestCase NVARCHAR(200), Result NVARCHAR(20), Msg NVARCHAR(MAX));
GO

CREATE PROCEDURE tSQLt.RunAll
AS
INSERT tSQLt.TestResult (Class, TestCase, Result, Msg)
VALUES (N'RegionTests', N'test regions are seeded', N'Success', NULL)
	,(N'RegionTests', N'test something optional', N'Skipped', N'not today');

RAISERROR (N'Test Case Summary: 2 test case(s) executed, 1 succeeded, 1 skipped, 0 failed, 0 errored.', 16, 1);
GO
