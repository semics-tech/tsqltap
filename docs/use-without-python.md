# Use tap from SSMS / sqlcmd without Python

The `tap` assertions are plain T-SQL: a `tap` schema of stored procedures. Python and pytest are
only the **runner** that finds `*.test.sql` files, wraps each in a transaction and turns every
assertion into a JUnit case. You can install `tap` into any database and call it from SSMS, Azure
Data Studio or `sqlcmd` with no Python at all.

You lose the runner's conveniences: no disposable container, no automatic rollback, no
per-assertion JUnit. You get the same assertions and mocks.

## 1. Get the install script

`tap.sql` is generated from the same files the runner installs, so it never drifts from them.

- From a release: download `tap.sql` from the release's assets.
- From the package (needs Python only for this one step):

  ```console
  $ pip install tsqltap
  $ tsqltap script -o tap.sql
  ```

## 2. Install it into a test database

Install it into a **test** database (a copy of dev, or a database you can throw away), never your
production database. It creates the `tap` schema and procedures, and can be re-run to upgrade.

```console
$ sqlcmd -S localhost -d MyTestDb -i tap.sql
```

In SSMS, open `tap.sql`, select your test database in the toolbar, and run it.

## 3. A worked example

Run this in SSMS or save it as `person_test.sql` and run `sqlcmd -S localhost -d MyTestDb -i person_test.sql`.
Everything happens in a transaction that is rolled back at the end, so nothing is left behind.

```sql
SET NOCOUNT ON;
BEGIN TRANSACTION;

CREATE TABLE dbo.Person (Id INT IDENTITY PRIMARY KEY, Name NVARCHAR(50) NOT NULL);
INSERT dbo.Person (Name) VALUES (N'Ada');

EXEC tap.row_count_is N'SELECT * FROM dbo.Person', 1, N'one person';
EXEC tap.results_eq N'SELECT Name FROM dbo.Person', N'VALUES (N''Ada'')', N'the name is stored';
EXEC tap.throws_ok N'INSERT dbo.Person (Name) VALUES (NULL)', 515, NULL, N'Name is required';

ROLLBACK TRANSACTION;
```

Each assertion returns one result row:

```
__tap  kind    ok  description          detail  todo
1      assert  1   one person           NULL    NULL
1      assert  1   the name is stored   NULL    NULL
1      assert  1   Name is required     NULL    NULL
```

`ok = 1` is a pass and `ok = 0` is a failure, with the reason in `detail`. In SSMS you will see
each as a result grid. With `sqlcmd`, add `-b` to stop on errors.

Notes:

- `tap` must already be installed in the database you run against.
- Wrap tests in a transaction and roll back, as above. `tap.fake_table` and `tap.spy_procedure`
  refuse to run outside a transaction.
- Because results are streamed back rather than stored, they survive a `ROLLBACK` inside the code
  under test.

## 4. Uninstall

When you are done, remove `tap` from the database. It only owns the `tap` schema, so this drops
every procedure and function in it, then the schema itself. Nothing else is touched.

```sql
DECLARE @sql NVARCHAR(MAX) = N'';

SELECT @sql += N'DROP ' + CASE WHEN o.type = 'P' THEN N'PROCEDURE ' ELSE N'FUNCTION ' END
             + QUOTENAME(s.name) + N'.' + QUOTENAME(o.name) + N';' + CHAR(10)
FROM sys.objects AS o
JOIN sys.schemas AS s ON s.schema_id = o.schema_id
WHERE s.name = N'tap' AND o.type IN ('P', 'FN', 'IF', 'TF');

EXEC sys.sp_executesql @sql;

IF SCHEMA_ID(N'tap') IS NOT NULL
    DROP SCHEMA tap;  -- errors if you put your own objects in the tap schema
```

Run it in the same database you installed into. To reinstall later, run `tap.sql` again.

## When to use the runner instead

Use `pytest` when you want a disposable container, snapshot isolation, seed data, and JUnit output
for CI. See the [README](https://github.com/semics-tech/tsqltap#readme).
