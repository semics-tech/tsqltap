-- tsqltap: seed=fixtures/trusts.yaml
/* Seed data from a YAML file, loaded inside the test's transaction: only the columns that matter. */
EXEC tap.results_eq N'SELECT t.TrustCode, t.RegionCode, t.Name FROM dbo.vw_ActiveTrust v JOIN dbo.Trust t ON t.TrustCode = v.TrustCode ORDER BY t.TrustCode'
	, N'VALUES (''A1'', ''E1'', N''Alpha''), (''B1'', ''E1'', N''Beta'')'
	, N'seeded trusts are visible in the view';
EXEC tap.row_count_is N'SELECT * FROM dbo.Region WHERE RegionCode = ''E1''', 1, N'parent region seeded first';
