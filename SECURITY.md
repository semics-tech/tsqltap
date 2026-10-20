# Security policy

## Supported versions

tsqltap is pre-1.0. Security fixes go into the latest released version only.

| Version | Supported |
| ------- | --------- |
| latest 0.x release | yes |
| older releases | no |

## Reporting a vulnerability

Please don't open a public issue for a security problem.

Report it privately through GitHub:
[Report a vulnerability](https://github.com/semics-tech/tsqltap/security/advisories/new).

Include what you found, the tsqltap version, and steps to reproduce. You should get a first reply
within a few days. If the report is confirmed, a fix is released and the advisory published, with
credit to you if you want it.

## Scope

tsqltap runs T-SQL you write against a disposable SQL Server container it starts for you. Issues
in tsqltap's own code are in scope (for example, unsafe handling of connection strings or
credentials, or command injection in the runner). Vulnerabilities in SQL Server, Docker or pytest
should be reported to their own maintainers.
