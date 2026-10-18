-- tsqltap: isolation=snapshot
/* tap.throws_ok on code that opens a transaction and raises inside it (XACT_ABORT ON). */
EXEC tap.throws_ok N'EXEC dbo.usp_OpensTransactionThenThrows', 50999, NULL, N'the error is caught';

DECLARE @tc INT = @@TRANCOUNT;
EXEC tap.[is] @tc, 0, N'and the transaction it opened is rolled back, as an uncaught error would have';
EXEC tap.is_empty N'SELECT * FROM dbo.Region WHERE RegionCode = ''X1''', N'with its work';
