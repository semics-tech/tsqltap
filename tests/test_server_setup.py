from tsqltap.provision import Environment


def test_server_setup_scripts_ran_in_master(mssql_env: Environment) -> None:
    with mssql_env.connect() as master:
        assert master.scalar("SELECT SUSER_ID(N'tsqltap_setup_probe')") is not None
