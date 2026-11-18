# sg-mcproperty

MCP layer on Singapore property transaction data stored in ClickHouse.

```
URA API --(sg-property-load --dump)--> output/*.parquet --(to_clickhouse)--> ClickHouse
                                                     migrations define schema ^   |
                                                              MCP server (read-only) <-- MCP client / LLM
```

Status: private residential transactions are fetched, transformed, dumped to parquet and
loaded into ClickHouse. The MCP server has ten tools (`search_projects`, `search_transactions`,
`price_trend`, `compare_nearby`, `rank_nearby_growth`, `affordability_search`, `dataset_info`,
`project_summary`, `market_activity`, `estimate_value`), three prompts and two resources for
that dataset. More tools are planned (see [TODO](#todo)).

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

### The rentals table

`0003_private_residential_rentals.sql` creates `sg_property.private_residential_rentals`: `project`,
`street`, `district` (UInt8), `property_type`, `lease_date` (DateTime, first of the month),
`area_sqft_min` and `area_sqft_max` (Nullable UInt32), `bedrooms` (Nullable UInt8), `rent` (Float64,
monthly SGD), `x` and `y` (Nullable Float64, SVY21 metres) and `loaded_at`. Like the sales table it
is a plain `MergeTree` ordered by `(district, project, lease_date)` and partitioned by year, and the
loader truncates it before each insert. No MCP tool reads it yet.

### The table

`0001_private_residential_transactions.sql` creates `sg_property.private_residential_transactions`, and
`0002_private_residential_location_and_type.sql` adds `property_type`, `x` and `y` (re-run the load
after migrating so they are filled in):

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
| `property_type` | `LowCardinality(String)` | URA type: Apartment, Condominium, Executive Condominium, Terrace, Semi-detached, Detached and the Strata variants |
| `x`, `y` | `Nullable(Float64)` | project location in SVY21 metres. Null for about 5% of projects but about 22% of transactions, mostly recent new launches |
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

### Rental contracts

```bash
uv run sg-property-load private-rental --dump
```

Same pipeline for URA's rental contracts (`PMI_Resi_Rental`, which serves one quarter at a time):

1. `URAClient.rental_contracts_many` fetches the latest 21 quarters (`refPeriod` such as `25q3`,
   newest first, 6 requests at a time). 21 quarters is URA's whole history (21q4 was the oldest
   with data in November 2026) and a quarter URA has not published yet just comes back empty.
2. `loaders/private_rental.py` flattens projects and their `rental` lists into one row per
   contract and converts types: `lease_date` from `mmyy`, the area band to `area_sqft_min` and
   `area_sqft_max` (`1700-1800`, or `>3000` and `<=500` with one side null), `bedrooms` (null when
   URA says `NA` or omits it), `rent` (monthly SGD) and the project's `x` and `y`.
3. `--dump` writes `output/private_rental.parquet` (about 450,000 rows, 3 MB, in about 10 seconds).

Rental property types are URA's rental wording (`Non-landed Properties`, `Terrace House`,
`Semi-Detached House`, `Detached House`, `Executive Condominium`), which differs from the sales
feed, so they are stored as given. About 13% of contracts have no bedroom count. Identical
contracts within a quarter are real separate rows (bands are coarse and there is no contract id),
so nothing is deduplicated.

## 6. Load into ClickHouse

```bash
uv run python -m sg_mcproperty.loaders.to_clickhouse private-residential
uv run python -m sg_mcproperty.loaders.to_clickhouse private-rental
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

## 7. Run the MCP server

```bash
uv run sg-property-mcp        # stdio MCP server, read-only against ClickHouse
```

What it exposes for private residential data:

| Kind | Name | Purpose |
|---|---|---|
| Tool | `search_projects` | Find exact project names from a fuzzy or misspelled name, optionally by district |
| Tool | `dataset_info` | What the data covers: counts, date range, valid districts, sale types and segments, coordinate coverage, and the rental contracts' coverage and overlap with projects. Call it to learn valid filter values and data freshness |
| Tool | `project_summary` | One project's profile: tenure, sales mix, price, psf and area ranges, and the latest 12 months versus the 12 before |
| Tool | `market_activity` | Sales per month, quarter or year split by sale type, with the volume change over complete periods. A liquidity and momentum check before trusting a price |
| Tool | `estimate_value` | Regression estimate of what a unit would sell for, with a range, plus a gross rental yield and income cross-check for the size (see below) |
| Tool | `search_transactions` | List transactions, newest first, filtered by project, district, segment, sale type, dates, price and area (capped at 100 rows, reports `total_matches`) |
| Tool | `price_trend` | Median price and psf per month, quarter or year, for a project, district or the market. Flags thin periods and reports the psf change |
| Tool | `compare_nearby` | Compares one project's psf with similar projects nearby to value it, adding size-matched rental yields when `area_sqft` is given (see below) |
| Tool | `rank_nearby_growth` | Ranks the nearest projects by annualised psf growth, using resales in the same window and size band; always confirms the scan scope first, and with `include_rental` ranks by gross total return instead |
| Tool | `affordability_search` | Projects with sales inside a budget, dearest median price first. Asks for a budget or works one out from income and loan assumptions (see below) |
| Prompt | `project_overview` | Guides the model through summarising one project's recent sales, liquidity and price trend |
| Prompt | `recent_deals` | Guides the model through ranking notable sales in a district, respecting the row cap |
| Prompt | `value_project` | Guides the model through a full assessment: liquidity, comparison with nearby projects, a unit estimate and the price trend |
| Resource | `property://private-residential/schema` | Column meanings, units and caveats |
| Resource | `property://private-residential/coverage` | Live row count, project count and date range |

### Valuing a project against nearby ones (`compare_nearby`)

```bash
python -m sg_mcproperty.server.dev_client call compare_nearby "project=treasure at tampines" radius_m=2000 months=24 area_sqft=1000 lease_year_tolerance=8
```

- **Peers** are projects of the same segment within `radius_m` metres (SVY21 distance), nearest first.
  Segments keep like with like: non-landed (condos and apartments), `ec`, `landed` and `strata-landed`.
- **Same conditions for both sides:** the project and its peers use the same window (`months`
  ending at the latest data month), sale type and size band (+/-20% of `area_sqft`).
- **Result:** the project's median psf, each peer's distance and psf, the pooled peer median and
  quartiles, `premium_pct` (project versus peer median) and, when `area_sqft` is given, an
  indicative price range from the peer 25th to 75th percentile psf.
- **Rental yield** (`rental`, and `gross_yield_pct` on the project and each peer) appears only when
  `area_sqft` is given, because a yield is only comparable between similar-sized units. It uses
  rents and Resale/Sub Sale prices inside +/-20% of that area, so the target and peers are measured
  on the same size band. Yields are gross: they ignore stamp duty, agent fees, property tax,
  maintenance, vacancy and income tax. `rental.peers_with_yield` says how many peers had enough
  data; the notes say why a yield is missing. With a `New Sale` comparison, yields still use resale
  prices (and the notes say so).
- **Age matters a lot.** Older projects price lower, so by default the notes warn when peers' lease
  start is more than 15 years from the project's. `lease_year_tolerance=8` limits peers to similar
  age (for Treasure at Tampines the premium over peers falls from about 58% to 3%).
  `same_tenure_type=true` restricts to freehold or leasehold like the project.
- **Missing coordinates:** about a fifth of transactions (22%), mostly new launches, belong to projects
  without coordinates. For those the peers are the busiest projects on the same street, then in
  the same district (`basis` says which, and `distance_m` is null).
- It is an indicative comparison of recorded prices, not a valuation: it ignores floor, view and
  unit condition.

### Estimating a unit's value (`estimate_value`)

```bash
python -m sg_mcproperty.server.dev_client call estimate_value "project=treasure at tampines" area_sqft=1000 floor=12
python -m sg_mcproperty.server.dev_client call estimate_value project=aurea area_sqft=700 "type_of_sale=new sale"
```

It fits a regression of log price on the last `months` (default 24) of sales in the project and
similar projects nearby (same property type, within `radius_m`, else the same street or district),
then predicts the unit you describe at the latest month of data. The result has a median, an 80%
range, the model fit, what it learned (percentage effects of size, floor, sale type and the monthly
trend), a sanity check against the project's own median psf, and notes.

- **Two modes** (`model.mode`). With at least 5 sales of its own the project gets its own price
  level ("project" mode): size, floor, sale-type and trend effects are learned from variation
  *within* projects, so project quality cannot leak into them, and the estimate sits at this
  project's own level. With fewer sales ("area" mode) the model compares across projects and the
  estimate reflects the surrounding area. The lease and freehold effects are only estimated in
  area mode, where they can be confounded with project quality.
- **Rental yield and income check.** Rentals are not a regression feature: the estimate stays a
  price model. Instead, when rentals are loaded, the result adds a `rental` block with the gross
  yield for units within +/-20% of `area_sqft` (12-month rent median over 24-month Resale/Sub Sale
  psf), and an `income_cross_check` that capitalises the unit's rent at the median yield of the
  comparable peer projects and compares that implied value with the regression estimate. The check
  is only offered with at least 3 peer projects and a plausible yield (0.5-8%), and a divergence of
  more than 20% is flagged. All yields are gross: they exclude stamp duty, agent fees, property
  tax, maintenance, vacancy and income tax.
- **Accuracy**, from `scripts/backtest_estimate.py` (fit on older sales, predict held-out later
  ones, 25 busy projects, four hold-out periods, about 1,200 held-out sales): the median absolute
  error was 3.4-4.1% and the 90th percentile 9-14%. The plain 80% interval was too narrow
  out of sample, so it is widened by 15% (`INTERVAL_SCALE`): coverage was then 74-85% by period
  and about 81% pooled. The most recent hold-out period was the worst (74% coverage and a -3%
  bias in a rising market), which is the period that matters most, so treat the range as a guide.
  Re-run the script after reloading data: `python scripts/backtest_estimate.py --holdout-start 20`.
- **It cannot see** view, facing, renovation or the exact unit, so it is a statistical estimate,
  not a valuation. Always read the notes: small samples, weak fit, extrapolation beyond the sizes
  or floors sold, and project mode versus area mode.
- It is plain Python on a few hundred rows (`server/private_residential/regression.py`), with no
  numpy dependency.

### Ranking neighbours by growth (`rank_nearby_growth`)

```bash
python -m sg_mcproperty.server.dev_client call rank_nearby_growth "project=duchess residences" radius_m=2000 min_area_sqft=1200 max_area_sqft=2000 "tenure=freehold or 999 years"
```

- Scans the nearest `max_peers` projects (default 50, within `radius_m`, default 2000), fits each
  project's annualised psf trend over `months` (default 60, the full dataset), then ranks the
  projects whose trend is measurable.
- Only **Resale and Sub Sale** units are used, so launch prices do not distort the trend. The same
  size band, window and tenure filter (`any`, `freehold`, `999 years`, `freehold or 999 years`)
  apply to the project and its peers.
- `annual_growth_pct` is a recorded-price trend, not a forecast: 4.5 means +4.5% a year. Projects
  with fewer than `min_transactions` resales (default 4) or fewer than 3 usable sales are returned
  after the ranked peers with no growth figure, so the coverage is visible.
- **Total return:** pass `include_rental=true` to rank by `total_return_pct` instead, which adds
  each project's gross rental yield, measured on units within +/-20% of the same reference area, to
  its realised annual growth. Without a size band, the reference area is the project's median sale
  area and the growth scan narrows to that band. If rentals are not loaded or no yield is usable,
  the ranking falls back to growth only and says so in `ranking_basis` and the notes. Total return
  mixes a current yield with a past growth rate: it is "what this asset returned lately", never a
  forecast, and it is before costs.
- Projects without coordinates cannot be scanned; use `compare_nearby` for those.

### Searching by budget (`affordability_search`)

```bash
python -m sg_mcproperty.server.dev_client call affordability_search max_price=1500000 district=15 min_area_sqft=700
```

- Returns projects with at least `min_transactions` sales (default 3) at or below the budget,
  dearest median price first, with their median and minimum price, median psf and area range.
- Omit `max_price` and the tool asks for a budget, or for a gross monthly income plus down payment,
  loan tenure and stress rate, and works one out. The calculation is illustrative: it counts 55% of
  gross monthly income (TDSR) at the stress rate, then grosses up for the down payment. It ignores
  existing debts, stamp duty, legal fees and renovation, so it is not a loan offer.

### Elicitation

`estimate_value` (no size), `compare_nearby` (`ask_basis=true`), `rank_nearby_growth` (always,
including whether to count rental yield) and `affordability_search` (no budget) ask for input
mid-call. The
helper in `server/elicitation.py` supports both protocol eras: handshake connections use
`ctx.elicit`, while the 2026-07-28 protocol returns an `InputRequiredResult` and the client retries
the call with the answers. Declining a form falls back to the arguments already passed, except for
`estimate_value` and `affordability_search`, where there is nothing to answer without it. A
handshake-era client that did not declare elicitation cannot be asked: `rank_nearby_growth` then
runs with the arguments as given.

### Trying the tools without an MCP client

```bash
python -m sg_mcproperty.server.dev_client list
python -m sg_mcproperty.server.dev_client call search_projects name_query=parktown district=18
python -m sg_mcproperty.server.dev_client call search_transactions project=aurea start_date=2026-01 limit=5
python -m sg_mcproperty.server.dev_client --transport stdio call search_projects name_query=aurea
```

The default `memory` transport runs the server inside the same process, so breakpoints in tool
code are hit under the debugger. `--transport stdio` starts a real server subprocess. Arguments
are `key=value` pairs or one JSON object. Run it with the `.env` variables exported.

### How the server code is layered

```
server/tools | prompts | resources   MCP adapters: signatures, docstrings, one call each
        v
server/private_residential/service.py      validation, limits, typed results
        v
server/private_residential/repository.py   SQL templates and parameters
server/private_rental/repository.py        same, for the rental contracts table
        v
accessor.py (ClickHouseAccessor)           read-only ClickHouse connection
```

`server/private_residential/filters.py` holds the pure input handling (normalising names,
validating districts and dates, building the `WHERE` clause). `server/app.py` wires the layers
together. SQL values always go through `{name:Type}` parameters, never string formatting.

The service also composes `server/private_rental/`, which owns the rental contracts table and
the pure yield maths in `returns.py`. Both repositories sit at the same layer; the service is
the only place that combines their rows, and `rental_repository` is optional so the server runs
without the rentals table loaded.

## 8. Cursor / VS Code launch configurations

`.vscode/launch.json` is gitignored, so it is local. It has five configurations, all using the
`.venv` interpreter and loading `.env`:

- **Load: private-residential** and **Load: private-rental**: `sg_mcproperty.loaders.cli <dataset> --dump`
- **Load output -> ClickHouse: private-rental**: `sg_mcproperty.loaders.to_clickhouse private-rental`
- **Load output -> ClickHouse: private-residential**: `sg_mcproperty.loaders.to_clickhouse`
- **Migrate: ClickHouse**: `sg_mcproperty.migrate`
- **MCP dev client: list tools** and **MCP dev client: call tool**: `sg_mcproperty.server.dev_client`
  (edit the `args` of the second one to try other tools)

Run these modules with `-m` or from the Run and Debug panel. Running a file such as `migrate.py`
with the play button fails with `ImportError: attempted relative import with no known parent
package`, because the code uses package-relative imports.

## 9. Tests and lint

```bash
uv run pytest tests/server tests/loaders/test_private_residential.py
uv run ruff check . && uv run ruff format .
```

`tests/server/private_residential/` mirrors the source (`test_filters.py`, `test_repository.py`,
`test_service.py`, `test_regression.py`), and `tests/server/private_rental/` covers the rental
repository and the yield maths. The service tests use a fake repository and the repository tests use a fake
accessor, so most tests need no database. A few tests run against live ClickHouse and skip
themselves when it is not running or the table is empty.

`tests/loaders/test_hdb_resale.py` currently fails at collection because the `hdb_resale`
loader does not exist in this repo.

## Repo layout

| Path | Purpose |
|---|---|
| `clickhouse/migrations/` | Versioned DDL |
| `src/sg_mcproperty/migrate.py` | Migration runner (`sg-property-migrate`) |
| `src/sg_mcproperty/loaders/client.py` | URA API client |
| `src/sg_mcproperty/loaders/private_residential.py` | `flatten` and `transform` for URA sales transactions |
| `src/sg_mcproperty/loaders/private_rental.py` | `flatten` and `transform` for URA rental contracts |
| `src/sg_mcproperty/loaders/cli.py` | `sg-property-load` (fetch, transform, dump) |
| `src/sg_mcproperty/loaders/to_clickhouse.py` | Parquet to ClickHouse |
| `src/sg_mcproperty/loaders/utils.py` | `output/` path and `dump_file` |
| `src/sg_mcproperty/{config,db}.py` | Env settings and ClickHouse client factory |
| `src/sg_mcproperty/accessor.py` | `ClickHouseAccessor`: read-only parameterised queries |
| `src/sg_mcproperty/server/app.py` | MCP server entry point and wiring (`sg-property-mcp`) |
| `src/sg_mcproperty/server/private_residential/` | `filters.py`, `repository.py`, `service.py` for the dataset |
| `src/sg_mcproperty/server/private_rental/` | Rental contracts `repository.py` and the pure yield maths (`returns.py`) |
| `src/sg_mcproperty/server/{tools,prompts,resources}/` | MCP adapters, one module per dataset |
| `src/sg_mcproperty/server/dev_client.py` | Terminal client for trying tools during development |
| `scripts/backtest_estimate.py` | Backtests `estimate_value` on held-out sales (accuracy and interval coverage) |
| `tests/server/` | Server tests, mirroring the source layout |
| `output/` | Parquet dumps (gitignored) |
| `clickhouse_db/` | Local ClickHouse server data (gitignored) |

## TODO

1. ~~Setup loading script~~ done for private residential. HDB resale loader still to do
2. ~~Setup tables~~ done for private residential
3. ~~Setup MCP tools~~ `search_projects`, `search_transactions`, `price_trend`,
   `compare_nearby`, `rank_nearby_growth`, `affordability_search`, `dataset_info`,
   `project_summary`, `market_activity` and `estimate_value` done. Still planned:
   `rank_projects`
4. ~~Keep URA `propertyType` and the coordinates~~ done. `typeOfArea` and `marketSegment` are still
   dropped. Fill in coordinates for projects URA leaves blank (for example by geocoding the street)
5. ~~Add URA rental contracts~~ table and loader done, and `estimate_value`, `compare_nearby`,
   `rank_nearby_growth` and `dataset_info` now use them for gross yields. Still to do: a way to
   estimate bedrooms per sale from the rental bands (`bedrooms` by project and area band). The rental
   coordinates do not help the sales gap: only 48 of the 150 sale projects with no coordinates are in
   the rentals with coordinates, covering 84 of about 27,800 affected sales
6. Optional `--load` flag on `sg-property-load` to dump and insert in one command
