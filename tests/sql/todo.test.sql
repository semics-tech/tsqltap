/* tap.todo_start / tap.todo_end: a known failure is reported as an expected failure, not a failure. */
EXEC tap.pass N'ordinary assertion before the TODO block';

EXEC tap.todo_start N'demo: a known bug, ticket to follow';
EXEC tap.[is] 1, 2, N'known failure inside a TODO block';
EXEC tap.todo_end;

EXEC tap.pass N'ordinary assertion after the TODO block';
