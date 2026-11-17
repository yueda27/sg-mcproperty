# loaders/

One module per source dataset, exposing `transform(df) -> df` (pure) and `load(path) -> int`.

- Keep `transform` free of I/O so it can be tested with small in-memory frames
  (see `tests/loaders/`).
- Output columns must match the table DDL in `clickhouse/migrations/` exactly (`COLUMNS`).
- Insert via `client.insert(rows, column_names=...)` (no pyarrow dependency).
- Loads must be re-runnable: tables are ReplacingMergeTree, so don't truncate or dedupe by hand.
- Raw files go in `data/` (gitignored); loaders take a path argument, never download implicitly.
- Register the loader in `cli.py`'s `LOADERS`.
