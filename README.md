# sg-mcproperty

MCP layer on Singapore property transaction data stored in ClickHouse.

```
URA API --(sg-property-load --dump)--> output/*.parquet --(to_clickhouse)--> ClickHouse
                                                     migrations define schema ^
```

Status: private residential transactions are fetched, transformed, dumped to parquet and
loaded into ClickHouse. The MCP tools are not written yet (see [TODO](#todo)).

## 1. Prerequisites

- Python 3.12+ and [uv](https://docs.astral.sh/uv/)
- ClickHouse, either the local `clickhouse` binary or Docker (`docker-compose.yml`)
- A URA Data Service access key (request one from URA, it goes in `.env`)

## 2. Configure

```bash
cp .env.example .env     # then fill in URA_ACCESS_KEY
uv sync --extra dev
```

`.env` (gitignored):

| Variable | Default | Purpose |
|---|---|---|
| `URA_ACCESS_KEY` | none, required | URA access key. The client fetches the daily token itself |
| `CLICKHOUSE_HOST` / `_PORT` | `localhost` / `8123` | HTTP interface used by `clickhouse-connect` |
| `CLICKHOUSE_USER` / `_PASSWORD` | `default` / empty | |
| `CLICKHOUSE_DATABASE` | `sg_property` | Created by the migration runner |

The code only reads `os.environ`. Export the variables in your shell (`set -a; . ./.env; set +a`)
or use the Cursor launch configurations, which load `.env` for you.

## 3. Start ClickHouse

**Local binary.** `clickhouse server` uses the directory it is started from as its data
directory, so start it from `clickhouse_db/` to keep the repo root clean (that folder is
gitignored):

```bash
mkdir -p clickhouse_db && cd clickhouse_db && clickhouse server
```

**Docker.** `docker compose up -d` (data lives in a named volume).

Check it is up: `curl localhost:8123/ping` should print `Ok.`

Stop the server before moving or deleting its data folder.

## 4. Create the schema (migrations)

```bash
uv run sg-property-migrate        # creates the database and applies pending migrations
```

- Migrations are `clickhouse/migrations/NNNN_name.sql`, applied in order, once each.
  Applied versions and checksums are stored in `sg_property.schema_migrations`.
- Never edit an applied migration (the runner raises on a checksum mismatch). Add the next
  numbered file instead. The current schema is still `0001` because nothing was released.
- One statement per `;`, and no `;` inside comments or strings.

### The table

`0001_private_residential_transactions.sql` creates `sg_property.private_residential_transactions`:

| Column | Type | Notes |
|---|---|---|
| `project`, `street` | `String` | |
| `area_sqft` | `Float64` | converted from URA sqm (x 10.7639) |
| `floor` | `Nullable(Int16)` | midpoint of URA floor range, `06-10` becomes 8. Null for landed |
| `contract_date` | `DateTime` | first of the month (URA gives `mmyy`). Sale date for New Sale, option exercised date for Resale and Sub Sale |
| `type_of_sale` | `LowCardinality(String)` | `New Sale`, `Sub Sale`, `Resale` |
| `price` | `Float64` | SGD |
| `district` | `UInt8` | |
| `tenure` | `LowCardinality(String)` | `99 years`, `999 years`, `Freehold`, ... |
| `lease_year` | `Nullable(UInt16)` | lease commencement year, null for freehold |
| `loaded_at` | `DateTime` | default `now()` |

It is a plain `MergeTree` ordered by `(district, project, contract_date)` and partitioned by year.
**It is deliberately not `ReplacingMergeTree`.** URA has no transaction id, so two sales with the
same project, month, floor, area and price are real separate rows. A replacing engine
deduplicates on the sort key and silently drops them (we lost about two thirds of the rows this
way). `ORDER BY` here is only for query speed.

## 5. Fetch and transform URA data

```bash
uv run sg-property-load private-residential --dump
```

This runs `loaders/cli.py`:

1. `loaders/client.py` (`URAClient`) gets a daily token, then fetches the 4
   `PMI_Resi_Transaction` batches in parallel.
2. `loaders/private_residential.py` flattens the nested projects into one row per transaction
   (`flatten`) and converts types and units (`transform`, using `floor_midpoint`,
   `format_tenure` and `extract_lease_year`).
3. `--dump` writes `output/private_residential.parquet`. `output/` is gitignored. Without
   `--dump` the data is fetched and counted but not saved.

Expect about 131,000 rows covering the last 5 years (URA's rolling window).

## 6. Load into ClickHouse

```bash
uv run python -m sg_mcproperty.loaders.to_clickhouse private-residential
```

`loaders/to_clickhouse.py` reads the parquet file, checks the columns match, **truncates the
table**, and inserts in 100,000-row chunks. The parquet file is treated as the full dataset, so
re-running gives the same result and never duplicates rows.

Verify:

```bash
curl -s "localhost:8123/?user=default" --data \
  "SELECT count(), uniqExact(project), min(contract_date), max(contract_date)
   FROM sg_property.private_residential_transactions FORMAT TSVWithNames"
```

## 7. Cursor / VS Code launch configurations

`.vscode/launch.json` is gitignored, so it is local. It has three configurations, all using the
`.venv` interpreter and loading `.env`:

- **Load: private-residential**: `sg_mcproperty.loaders.cli private-residential --dump`
- **Load output -> ClickHouse: private-residential**: `sg_mcproperty.loaders.to_clickhouse`
- **Migrate: ClickHouse**: `sg_mcproperty.migrate`

Run these modules with `-m` or from the Run and Debug panel. Running a file such as `migrate.py`
with the play button fails with `ImportError: attempted relative import with no known parent
package`, because the code uses package-relative imports.

## 8. Tests and lint

```bash
uv run pytest tests/loaders/test_private_residential.py
uv run ruff check . && uv run ruff format .
```

`tests/loaders/test_hdb_resale.py` currently fails at collection because the `hdb_resale`
loader does not exist in this repo.

## Repo layout

| Path | Purpose |
|---|---|
| `clickhouse/migrations/` | Versioned DDL |
| `src/sg_mcproperty/migrate.py` | Migration runner (`sg-property-migrate`) |
| `src/sg_mcproperty/loaders/client.py` | URA API client |
| `src/sg_mcproperty/loaders/private_residential.py` | `flatten` and `transform` for URA transactions |
| `src/sg_mcproperty/loaders/cli.py` | `sg-property-load` (fetch, transform, dump) |
| `src/sg_mcproperty/loaders/to_clickhouse.py` | Parquet to ClickHouse |
| `src/sg_mcproperty/loaders/utils.py` | `output/` path and `dump_file` |
| `src/sg_mcproperty/{config,db}.py` | Env settings and ClickHouse client factory |
| `output/` | Parquet dumps (gitignored) |
| `clickhouse_db/` | Local ClickHouse server data (gitignored) |

## TODO

1. ~~Setup loading script~~ done for private residential. HDB resale loader still to do
2. ~~Setup tables~~ done for private residential
3. Setup MCP tools (read-only, parameterised) in `src/sg_mcproperty/server/tools/`
4. Add URA rental contracts (`noOfBedRoom`) to estimate bedrooms per transaction
5. Optional `--load` flag on `sg-property-load` to dump and insert in one command
