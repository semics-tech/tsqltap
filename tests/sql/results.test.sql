/* Result-set comparisons, all passing. Region holds the baseline seed (N1 North, S1 South). */
SET NOCOUNT ON;

EXEC tap.results_eq N'SELECT RegionCode, Name FROM dbo.Region ORDER BY RegionCode'
	, N'VALUES (''N1'', N''North''), (''S1'', N''South'')'
	, N'results_eq: ordered rows from VALUES';

EXEC tap.results_eq N'SELECT RegionCode FROM dbo.Region ORDER BY RegionCode'
	, N'SELECT ''N1'' UNION ALL SELECT ''S1'''
	, N'results_eq: query vs query';

EXEC tap.bag_eq N'SELECT Name FROM dbo.Region'
	, N'VALUES (N''South''), (N''North'')'
	, N'bag_eq: any order';

EXEC tap.set_eq N'SELECT RegionCode FROM dbo.Region CROSS JOIN (VALUES (1), (2)) d (n)'
	, N'VALUES (''S1''), (''N1'')'
	, N'set_eq: duplicates ignored';

EXEC tap.is_empty N'SELECT * FROM dbo.Trust', N'is_empty: no trusts yet';
EXEC tap.row_count_is N'SELECT * FROM dbo.Region', 2, N'row_count_is: two regions';

INSERT dbo.Trust (TrustCode, RegionCode, Name, OpenedOn)
VALUES ('T1', 'N1', N'One', '2026-01-01')
	,('T2', 'N1', N'Two', '2026-01-02');

-- Procedures work as @have when their first result set can be described.
EXEC tap.results_eq N'EXEC dbo.usp_TrustsInRegion @RegionCode = ''N1'''
	, N'VALUES (''T1'', N''One''), (''T2'', N''Two'')'
	, N'results_eq: EXEC of a procedure';

-- bag_eq: a trigger sees `inserted` in no particular order.
EXEC tap.bag_eq N'SELECT TrustCode, Action FROM dbo.TrustAudit'
	, N'VALUES (''T1'', ''insert''), (''T2'', ''insert'')'
	, N'the audit trigger fired for both rows';

EXEC tap.results_eq N'SELECT OpenedOn FROM dbo.Trust ORDER BY TrustCode'
	, N'VALUES (''2026-01-01''), (''2026-01-02'')'
	, N'results_eq: string literals convert to the column type';

EXEC tap.results_eq N'SELECT Score FROM dbo.Trust ORDER BY TrustCode'
	, N'VALUES (NULL), (NULL)'
	, N'results_eq: NULLs match';
