"""`python -m sg_mcproperty.loaders.to_clickhouse <dataset>`: parquet in output/ -> ClickHouse.

Reads the file written by `sg-property-load <dataset> --dump` and inserts it into the
dataset's table. The table is a plain MergeTree (no dedup), so each run TRUNCATEs it
first: the parquet file is the full dataset, and re-running gives the same result.
"""

import argparse

import polars as pl
from loguru import logger

from sg_mcproperty.config import Settings
from sg_mcproperty.db import get_client
from sg_mcproperty.loaders import private_residential
from sg_mcproperty.loaders.utils import OUTPUT_DIR

CHUNK_ROWS = 100_000

DATASETS = {
    "private-residential": {
        "file": "private_residential.parquet",
        "table": "private_residential_transactions",
        "columns": private_residential.COLUMNS,
    }
}


def read_output(file_name: str, columns: list[str]) -> pl.DataFrame:
    """Read a parquet file from output/, checking it has exactly the expected columns."""
    path = OUTPUT_DIR / file_name
    if not path.exists():
        raise FileNotFoundError(f"{path} not found: run `sg-property-load ... --dump` first")
    df = pl.read_parquet(path)
    if set(df.columns) != set(columns):
        raise ValueError(
            f"{file_name} columns {sorted(df.columns)} do not match expected {sorted(columns)}"
        )
    return df.select(columns)


def insert_dataframe(
    df: pl.DataFrame, table: str, settings: Settings | None = None, chunk_rows: int = CHUNK_ROWS
) -> int:
    """Replace the contents of `<database>.<table>` with `df`, inserting in chunks.

    Returns the number of rows sent.
    """
    settings = settings or Settings.from_env()
    client = get_client(settings)
    try:
        client.command(f"TRUNCATE TABLE {settings.database}.{table}")
        for start in range(0, df.height, chunk_rows):
            chunk = df.slice(start, chunk_rows)
            client.insert(
                f"{settings.database}.{table}",
                chunk.rows(),
                column_names=chunk.columns,
            )
            logger.info(f"Inserted rows {start}-{start + chunk.height} into {table}")
    finally:
        client.close()
    return df.height


def load_output(dataset: str, settings: Settings | None = None) -> int:
    spec = DATASETS[dataset]
    df = read_output(spec["file"], spec["columns"])
    return insert_dataframe(df, spec["table"], settings)


def main() -> None:
    parser = argparse.ArgumentParser(prog="sg-property-to-clickhouse")
    parser.add_argument("dataset", choices=sorted(DATASETS))
    args = parser.parse_args()
    rows = load_output(args.dataset)
    print(f"Inserted {rows} rows into {DATASETS[args.dataset]['table']}")


if __name__ == "__main__":
    main()
