import re
from typing import Any

import polars as pl

from sg_mcproperty.loaders.client import URAClient

SQM_TO_SQFT = 10.7639

COLUMNS = [
    "project",
    "street",
    "area_sqft",
    "floor",
    "contract_date",
    "type_of_sale",
    "price",
    "district",
    "tenure",
    "lease_year",
    "property_type",
    "x",
    "y",
]


TYPE_OF_SALE = {"1": "New Sale", "2": "Sub Sale", "3": "Resale"}

TXN_FIELDS = (
    "area",
    "floorRange",
    "contractDate",
    "typeOfSale",
    "price",
    "district",
    "tenure",
    "propertyType",
)
# `x` and `y` (SVY21 metres) live on the project, like `street`.
RAW_FIELDS = ("project", "street", "x", "y", *TXN_FIELDS)


def flatten(projects: list[dict[str, Any]]) -> pl.DataFrame:
    """One row per transaction, all values as strings (cast happens in `transform`).

    URA nests transactions under each project; `street` lives on the project while
    district, tenure, area etc. live on each transaction.
    """
    rows = [
        {
            "project": project.get("project"),
            "street": project.get("street"),
            "x": project.get("x"),
            "y": project.get("y"),
            **{f: txn.get(f) for f in TXN_FIELDS},
        }
        for project in projects
        for txn in project.get("transaction") or []
    ]
    return pl.DataFrame(rows, schema={f: pl.String for f in RAW_FIELDS})


_FLOOR_RANGE = re.compile(r"^\s*(\d+)\s*-\s*(\d+)\s*$")


def floor_midpoint(floor_range: str | None) -> int | None:
    """Convert one URA floor range to its midpoint, e.g. "06-10" -> 8, "01-05" -> 3.

    Returns None when the range isn't numeric (landed property uses "-"; basement
    ranges like "B1-B5" are also unsupported).
    """
    if floor_range is None:
        return None
    match = _FLOOR_RANGE.match(floor_range)
    if match is None:
        return None
    low, high = int(match.group(1)), int(match.group(2))
    return (low + high) // 2


_LEASE_YEAR = re.compile(r"commencing from (\d{4})")


def extract_lease_year(tenure: str | None) -> int | None:
    """Lease commencement year from one URA tenure string.

    "99 yrs lease commencing from 2024" -> 2024. Returns None for "Freehold" or any
    tenure without a commencement year.
    """
    if tenure is None:
        return None
    match = _LEASE_YEAR.search(tenure)
    return int(match.group(1)) if match else None


_TENURE_YEARS = re.compile(r"^\s*(\d+)\s*(?:yrs?|years?)\b", re.IGNORECASE)


def format_tenure(tenure: str | None) -> str | None:
    """Shorten one URA tenure string to its length in years.

    "99 yrs lease commencing from 2024" -> "99 years". Tenures without a number of
    years (e.g. "Freehold") are returned stripped but otherwise unchanged.
    """
    if tenure is None:
        return None
    match = _TENURE_YEARS.match(tenure)
    return f"{match.group(1)} years" if match else tenure.strip()


def transform(df: pl.DataFrame) -> pl.DataFrame:
    """Flattened all-string frame -> typed frame matching `COLUMNS`.

    - area_sqft: URA `area` is sqm, converted to sqft.
    - floor: midpoint of `floorRange` (e.g. "06-10" -> 8). Null when the range is not
      numeric (landed property uses "-"; basement ranges like "B1-B5" are also null).
    - contract_date: URA `mmyy` (e.g. "1215" = Dec 2015) -> first of that month, as a
      Datetime. It is the date of sale for New Sale records and the option exercised
      date for Resale and Sub Sale records.
    - type_of_sale: URA code 1/2/3 -> "New Sale" / "Sub Sale" / "Resale"; null if the
      code is unknown.
    - property_type: URA `propertyType`, e.g. "Condominium" or "Terrace House".
    - x, y: project location in SVY21 metres (distances between projects can use plain
      Euclidean distance). Null when URA has no usable coordinate.
    - tenure / lease_year: "99 yrs lease commencing from 2024" -> tenure "99 years" and
      lease_year 2024. Freehold has no lease year (null) and keeps its tenure text.
    """
    return df.select(
        pl.col("project").str.strip_chars(),
        pl.col("street").str.strip_chars(),
        (pl.col("area").cast(pl.Float64) * SQM_TO_SQFT).alias("area_sqft"),
        pl.col("floorRange").map_elements(floor_midpoint, return_dtype=pl.Int64).alias("floor"),
        pl.col("contractDate")
        .str.strip_chars()
        .str.strptime(pl.Datetime, "%m%y")
        .alias("contract_date"),
        pl.col("typeOfSale")
        .str.strip_chars()
        .replace_strict(TYPE_OF_SALE, default=None, return_dtype=pl.String)
        .alias("type_of_sale"),
        pl.col("price").cast(pl.Float64),
        pl.col("district").cast(pl.Int64),
        pl.col("tenure").map_elements(format_tenure, return_dtype=pl.String),
        pl.col("tenure")
        .map_elements(extract_lease_year, return_dtype=pl.Int64)
        .alias("lease_year"),
        pl.col("propertyType").str.strip_chars().alias("property_type"),
        pl.col("x").cast(pl.Float64, strict=False),
        pl.col("y").cast(pl.Float64, strict=False),
    )


def load():
    with URAClient() as ura:
        projects = ura.private_transactions()
    return transform(flatten(projects))


if __name__ == "__main__":
    data = load()
    print("HELLO")
