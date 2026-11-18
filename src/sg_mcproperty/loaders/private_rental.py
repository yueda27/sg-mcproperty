"""URA private residential rental contracts: flatten, transform and load.

Same shape as `private_residential.py` (sales). URA serves rentals one quarter at a time
(`refPeriod`, e.g. "25q3"), as projects with a nested `rental` list.
"""

import re
from datetime import date
from typing import Any

import polars as pl

from sg_mcproperty.loaders.client import URAClient

# URA keeps about five years: 21q4 was the oldest quarter with data in November 2026.
DEFAULT_QUARTERS = 21

COLUMNS = [
    "project",
    "street",
    "district",
    "property_type",
    "lease_date",
    "area_sqft_min",
    "area_sqft_max",
    "bedrooms",
    "rent",
    "x",
    "y",
]

RENTAL_FIELDS = ("areaSqft", "leaseDate", "propertyType", "district", "noOfBedRoom", "rent")
# `x` and `y` (SVY21 metres) live on the project, like `street`. Some projects have none.
RAW_FIELDS = ("project", "street", "x", "y", *RENTAL_FIELDS)


def recent_quarters(count: int, today: date | None = None) -> list[str]:
    """The latest `count` quarters as URA refPeriods, newest first: ["26q4", "26q3", ...]."""
    today = today or date.today()
    index = today.year * 4 + (today.month - 1) // 3
    return [f"{(i // 4) % 100:02d}q{i % 4 + 1}" for i in range(index, index - count, -1)]


def flatten(results: dict[str, list[dict[str, Any]]]) -> pl.DataFrame:
    """One row per rental contract, all values as strings (cast happens in `transform`).

    `results` maps each quarter to URA's projects for it. Rentals without a bedroom count have
    no `noOfBedRoom` key at all, which becomes null.
    """
    rows = [
        {
            "project": project.get("project"),
            "street": project.get("street"),
            "x": project.get("x"),
            "y": project.get("y"),
            **{f: _as_text(rental.get(f)) for f in RENTAL_FIELDS},
        }
        for projects in results.values()
        for project in projects
        for rental in project.get("rental") or []
    ]
    return pl.DataFrame(rows, schema={f: pl.String for f in RAW_FIELDS})


def _as_text(value: Any) -> str | None:
    return None if value is None else str(value)


_BAND = re.compile(r"^\s*(\d+)\s*-\s*(\d+)\s*$")
_OPEN_TOP = re.compile(r"^\s*>\s*(\d+)\s*$")
_OPEN_BOTTOM = re.compile(r"^\s*<=?\s*(\d+)\s*$")


def parse_area_band(band: str | None) -> tuple[int | None, int | None]:
    """Convert one URA area band to (min, max).

    "1700-1800" -> (1700, 1800), ">3000" -> (3000, None), "<=500" -> (None, 500). Anything
    else, including None, gives (None, None).
    """
    if band is None:
        return None, None
    if match := _BAND.match(band):
        return int(match.group(1)), int(match.group(2))
    if match := _OPEN_TOP.match(band):
        return int(match.group(1)), None
    if match := _OPEN_BOTTOM.match(band):
        return None, int(match.group(1))
    return None, None


def parse_bedrooms(value: str | None) -> int | None:
    """Bedroom count from URA's `noOfBedRoom`: "3" -> 3, while "NA" or missing -> None."""
    if value is None:
        return None
    value = value.strip()
    return int(value) if value.isdigit() else None


def transform(df: pl.DataFrame) -> pl.DataFrame:
    """Flattened all-string frame -> typed frame matching `COLUMNS`.

    - lease_date: URA `mmyy` -> first of that month, as a Datetime.
    - area_sqft_min / area_sqft_max: the area band; open ended bands leave one side null.
    - bedrooms: null when URA says "NA" or leaves it out.
    - rent: monthly rent in SGD.
    - x, y: project location in SVY21 metres, null when URA has none.
    """
    return df.select(
        pl.col("project").str.strip_chars(),
        pl.col("street").str.strip_chars(),
        pl.col("district").cast(pl.Int64),
        pl.col("propertyType").str.strip_chars().alias("property_type"),
        pl.col("leaseDate").str.strip_chars().str.strptime(pl.Datetime, "%m%y").alias("lease_date"),
        pl.col("areaSqft")
        .map_elements(lambda band: parse_area_band(band)[0], return_dtype=pl.Int64)
        .alias("area_sqft_min"),
        pl.col("areaSqft")
        .map_elements(lambda band: parse_area_band(band)[1], return_dtype=pl.Int64)
        .alias("area_sqft_max"),
        pl.col("noOfBedRoom").map_elements(parse_bedrooms, return_dtype=pl.Int64).alias("bedrooms"),
        pl.col("rent").cast(pl.Float64),
        pl.col("x").cast(pl.Float64, strict=False),
        pl.col("y").cast(pl.Float64, strict=False),
    )


def load(quarters: int = DEFAULT_QUARTERS):
    with URAClient() as ura:
        results = ura.rental_contracts_many(recent_quarters(quarters))
    return transform(flatten(results))


if __name__ == "__main__":
    data = load()
