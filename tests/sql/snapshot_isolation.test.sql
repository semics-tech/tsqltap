-- tsqltap: isolation=snapshot
/*
Code that ends the caller's transaction cannot run under the default isolation. Under
isolation=snapshot there is no wrapping transaction: the database is restored from its snapshot
after the file instead. tests/test_isolation.py checks the region added here did not survive.
*/
EXEC dbo.usp_AddRegionOrRollBack @RegionCode = 'W1', @Name = N'West';
EXEC dbo.usp_AddRegionOrRollBack @RegionCode = 'Z1', @Name = N'bad';

EXEC tap.row_count_is N'SELECT * FROM dbo.Region WHERE RegionCode = ''W1''', 1, N'committed region is there';
EXEC tap.is_empty N'SELECT * FROM dbo.Region WHERE RegionCode = ''Z1''', N'rolled-back region is not';
