/* [tool.tsqltap] finalize: runs after the baseline seed, before the snapshot. Records what it saw. */
CREATE TABLE dbo.FinalizeMarker (SeededRegions INT NOT NULL);

INSERT dbo.FinalizeMarker (SeededRegions)
SELECT COUNT(*) FROM dbo.Region;
