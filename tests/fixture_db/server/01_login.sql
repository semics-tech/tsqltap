/* Server-level setup ([tool.tsqltap] setup): runs in master before any database is built, every session. */
IF SUSER_ID(N'tsqltap_setup_probe') IS NULL
	CREATE LOGIN tsqltap_setup_probe WITH PASSWORD = N'Probe!Passw0rd', CHECK_POLICY = OFF;
