# sg-mcproperty

An MCP server that lets users ask natural-language questions about Singapore property
prices and trends. Data lives in a local ClickHouse; the server exposes parameterised
tools over it. Three independent parts, wired together only through ClickHouse tables:

```
data/ (raw files) --loaders--> ClickHouse <--migrations define schema
                                   ^
                                   +-- MCP server (read-only) <-- MCP client / LLM
```

## Layout

| Path | Purpose |
|------|---------|
| `clickhouse/migrations/NNNN_name.sql` | Versioned DDL, applied in order by `sg-property-migrate` |
| `src/sg_mcproperty/migrate.py` | Migration runner (tracks applied files + checksums in `schema_migrations`) |
| `src/sg_mcproperty/loaders/` | One module per dataset + `cli.py` registry (`sg-property-load`) |
| `src/sg_mcproperty/server/` | FastMCP app (`app.py`) and tool modules (`tools/`) |
| `src/sg_mcproperty/{config,db}.py` | Env-based settings and the shared ClickHouse client factory |
| `data/` | Raw downloads, gitignored (only `.gitkeep` is tracked) |
| `tests/` | Mirrors `src/` (`loaders/`, `server/`) |

## Commands

```bash
docker compose up -d                 # local ClickHouse (HTTP :8123)
cp .env.example .env                 # then export vars; code reads os.environ only
uv sync --extra dev
uv run sg-property-migrate           # create DB + apply pending migrations
uv run sg-property-load hdb-resale data/<file>.csv
uv run sg-property-mcp               # stdio MCP server
uv run pytest
uv run ruff check . && uv run ruff format .
```

## Conventions

- **Migrations are append-only.** Never edit an applied file (the runner raises on a
  checksum mismatch); add the next `NNNN_*.sql`. Use fully-qualified `sg_property.<table>`
  names and `IF NOT EXISTS`. One statement per `;`; no `;` inside comments or strings.
- **Tables use `ReplacingMergeTree(loaded_at)`** so re-running a loader is safe. Merges are
  async, so queries needing exact counts/medians must use `FINAL`.
- **Loaders split `transform` (pure polars, unit-tested) from `load` (I/O).** Read source
  CSVs as all-strings and cast explicitly in `transform`. Register new loaders in
  `loaders/cli.py`.
- **MCP tools are read-only and parameterised.** Use `get_client(readonly=True)` and
  ClickHouse `{name:Type}` query parameters; never interpolate user/LLM input into SQL. Do
  not add a free-form "run SQL" tool without discussing it first.
- Tool docstrings are what the LLM sees: state units (SGD, sqm), valid filter values, and
  defaults. Add one `tools/<topic>.py` per area exposing `register(mcp)`, then call it in
  `server/app.py`.
- Prices are SGD. HDB `month` is stored as the first day of the month.

## Datasets

- **HDB resale** (data.gov.sg CSV) -> `hdb_resale_transactions`. Loader implemented.
- **Private property** (URA) -> `private_property_transactions`. Table exists; **loader and
  MCP tools not yet written** (URA API needs an access key and daily token; `contractDate`
  is `mmyy`).

## Status / gotchas

- Nothing has been run against a live ClickHouse yet; migrations, `FINAL` queries and the
  `readonly=1` client setting are untested end to end.
- `.claude/CLAUDE.md` is the repo-wide file; component details are in `CLAUDE.md` files next
  to the code.
