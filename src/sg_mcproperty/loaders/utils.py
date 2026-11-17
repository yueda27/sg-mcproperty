from pathlib import Path

import polars as pl

# utils.py -> loaders/ -> sg_mcproperty/ -> src/ -> project root
PROJECT_ROOT = Path(__file__).resolve().parents[3]
OUTPUT_DIR = PROJECT_ROOT / "output"


def dump_file(df: pl.DataFrame, file_name: str) -> bool:
    assert file_name.endswith(".parquet"), "Filename needs to end with .parquet"
    OUTPUT_DIR.mkdir(exist_ok=True)
    df.write_parquet(OUTPUT_DIR / file_name)
    return True
