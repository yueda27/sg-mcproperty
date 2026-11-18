---
name: add-mcp-tool
description: Add or change an MCP tool, prompt or resource in this repo while keeping the layered architecture (filters -> repository -> service -> MCP adapters). Use whenever editing anything under src/sg_mcproperty/server/, adding a dataset to the server, or reviewing server code for architecture violations.
---

# Adding to the MCP server

The server is layered. Each layer only calls the one below it. Keep it that way.

```
server/tools | prompts | resources      MCP adapters
        v
server/<dataset>/service.py             use cases
        v
server/<dataset>/repository.py          SQL
        v
accessor.py (ClickHouseAccessor)        database connection
```

`server/<dataset>/filters.py` holds pure input handling and sits beside the service. The
reference implementation is `server/private_residential/`. Read it before writing anything new,
and copy its shape.

## Layer rules

| Layer | May contain | Must NOT contain |
|---|---|---|
| `tools/`, `prompts/`, `resources/` | `@mcp.tool` / `@mcp.prompt` / `@mcp.resource` functions, signatures, docstrings, a one-line call into the service | SQL, validation, limits, row shaping, `ClickHouseAccessor` |
| `service.py` | validation calls, limit clamping, result shaping, typed result classes (`TypedDict`), dates to ISO strings | SQL text, `fastmcp` imports, accessor calls |
| `repository.py` | `string.Template` SQL constants, parameter dicts, calls to the accessor, raw rows back | business rules, validation messages, `fastmcp` imports |
| `filters.py` | pure functions and frozen dataclasses that normalise and validate inputs, raise `ValueError` with caller-friendly messages | I/O of any kind, SQL execution |
| `accessor.py` | connection handling, `query()`, `table()` | dataset knowledge |

`server/app.py` is the only place that builds objects and wires layers (accessor -> repository ->
service -> `register(mcp, ...)`). Do not construct accessors or repositories inside tools.

## Steps to add a tool

Do these in order, bottom up, and run the tests after each step.

1. **Filters** (if the tool takes inputs that need validating): add or extend a function or frozen
   dataclass in `filters.py`. Normalise (strip, upper-case, parse dates) and validate here.
   Unit test it with no fakes.
2. **Repository**: add a `string.Template` SQL constant and one method that runs it.
   - Only `$table` and fixed fragments are substituted with `$name`.
   - Every value, including lists, goes through a `{name:Type}` parameter.
   - Return the accessor's raw rows. No renaming, no clamping, no ISO dates.
   - Aggregate in SQL. Never return unbounded rows: always `LIMIT {limit:UInt32}`.
3. **Service**: add a method that validates, clamps the limit (define a `MAX_*` constant), calls
   the repository, and shapes the result into a typed `TypedDict`. Convert `datetime` to ISO strings
   here.
4. **Export** new public names from the package `__init__.py` if the adapters need them.
5. **Tool adapter**: add `@mcp.tool` in `server/tools/<dataset>.py`. The body is one call into the
   service, plus building a filters object if needed. Return the typed result so FastMCP publishes
   an output schema.
6. **Prompt or resource** (only if useful): prompts return instructions that name tools that already
   exist. Resources are reference text or small status documents. Neither contains SQL or calls the
   accessor.
7. **Tests** (see below) and a **dev client check** against live ClickHouse.
8. **Docs**: update the tool table in `README.md` section 7 and the TODO list.

## Tool design rules

- **Read-only.** The accessor defaults to `readonly=True`. Never add a free-form "run SQL" tool.
- **Parameterised SQL only.** Never build SQL with f-strings or `%` formatting from user or LLM
  input. A test must assert that hostile input (for example `x'; DROP TABLE t; --`) stays out of the
  SQL text and appears only in the parameters.
- **Bounded output.** Cap rows, report `total_matches` or similar so the model knows when a list is
  truncated, and aggregate in SQL instead of dumping rows.
- **The docstring is the contract with the LLM.** State units (SGD, sqft), valid values
  (`type_of_sale` is "New Sale", "Sub Sale" or "Resale"; districts are 1-28), date formats,
  defaults, caps, and what each returned field means. Say which tool to call first when names must
  be exact (`search_projects`). Say when a result means "no match" rather than an error.
- **Typed signatures.** Use real type hints (`int | None`, `float | None`). They become the input
  schema. Return a `TypedDict`, not a bare `dict`.
- **Domain caveats belong in docstrings and the schema resource**: `contract_date` is month-level
  (sale month for New Sale, option exercised month for Resale and Sub Sale), landed property has no
  floor and its psf is not comparable with condos, and there is no bedroom count in the data.
- **Names**: tool names are `verb_noun` in snake_case. One `tools/<dataset>.py` per dataset exposing
  `register(mcp, service)`.
- **Plain `MergeTree` table**: no `FINAL`. Only use it on a `ReplacingMergeTree`.

## Tests

Tests mirror the source under `tests/server/<dataset>/`:

| File | Tests | Uses |
|---|---|---|
| `test_filters.py` | normalising, validation errors, `to_sql()` output | nothing, pure |
| `test_repository.py` | SQL shape, parameters, no leftover `$`, hostile input only in params | `FakeAccessor` |
| `test_service.py` | fallbacks, limit clamping, validation, result shaping | `FakeRepository` |
| `tests/server/test_app.py` | tools, prompts and resources are registered and callable end to end | stub service + in-memory `Client` |

- Service tests must never assert on SQL text. Repository tests must never assert on business rules.
- Shared fakes and row builders live in `tests/server/<dataset>/helpers.py`.
- Add at least one live test (it skips itself when ClickHouse is down or the table is empty) for any
  new query, since the fakes cannot prove the SQL runs.

## Verify before saying it is done

```bash
set -a; . ./.env; set +a                        # ClickHouse settings from .env
uv run pytest tests/server -q -rs                # skips show up here, read them
uv run ruff check src tests && uv run ruff format --check src tests
python -m sg_mcproperty.server.dev_client list                      # new tool and docstring visible
python -m sg_mcproperty.server.dev_client call <tool> key=value     # real result, sensible values
python -m sg_mcproperty.server.dev_client --transport stdio call <tool> key=value
```

Cross-check one number against a direct query (for example `curl localhost:8123 --data "SELECT
count() ..."`). If ClickHouse is not running, say the live checks were not run instead of claiming
the tool works.

## Review checklist (use this when reviewing server changes)

- [ ] No SQL, accessor or `ClickHouseAccessor` import in `tools/`, `prompts/`, `resources/`
- [ ] No `fastmcp` import in `service.py`, `repository.py` or `filters.py`
- [ ] No arrow pointing up: `repository` never imports `service`, `filters` imports nothing from the package
- [ ] All values parameterised, only `$table` and fixed fragments in templates
- [ ] Every query has a `LIMIT` or is an aggregate returning a handful of rows
- [ ] Limits clamped in the service with a named `MAX_*` constant
- [ ] Results are `TypedDict`s with dates as ISO strings
- [ ] Docstring states units, valid values, defaults, caps and return fields
- [ ] Tests exist at each layer, including one hostile-input test and one live test
- [ ] `README.md` tool table updated

## Adding a second dataset (for example HDB resale)

Create `server/<dataset>/` with its own `filters.py`, `repository.py`, `service.py`,
`__init__.py`, then `tools/<dataset>.py`, and `prompts/` and `resources/` modules if needed. Wire it
in `server/app.py`. Do not add shared base classes until a second dataset shows what is actually
common. Reuse `ClickHouseAccessor` and the `Queryable` protocol as they are.
