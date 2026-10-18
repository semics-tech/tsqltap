-- tsqltap: requires=azure
/* Skipped: the container is the box engine, not Azure SQL Database. */
EXEC tap.fail N'should never run';
