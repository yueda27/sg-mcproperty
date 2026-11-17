# clickhouse/

Schema as code. Only `migrations/*.sql` lives here; the runner is `src/sg_mcproperty/migrate.py`.

- Name files `NNNN_snake_case.sql` (zero-padded, strictly increasing). Applied in sorted order.
- Never modify a file that has been applied anywhere; add a new migration (e.g. `ALTER TABLE`).
- Qualify tables as `sg_property.<table>`. The runner creates the database itself.
- Pick `ORDER BY` for the dominant filters (town/district, type, month); partition by year.
- Statements are split on `;`, so keep `;` out of comments and string literals.
