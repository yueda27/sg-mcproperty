"""Input handling for private residential tools: normalising, validating, filter object.

Pure code with no database and no MCP, so it is fast to unit test. Raises ValueError with
messages meant to be shown to the caller.
"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

MIN_QUERY_CHARS = 2
DISTRICTS = range(1, 29)
TYPES_OF_SALE = {"NEW SALE": "New Sale", "SUB SALE": "Sub Sale", "RESALE": "Resale"}


def _squash(text: str) -> str:
    """Upper-case and collapse whitespace, the form project names are stored in."""
    return " ".join(text.upper().split())


def normalise_project_query(name_query: str) -> tuple[str, list[str]]:
    """Normalise a project name query. Returns (query, words)."""
    query = _squash(name_query)
    if len(query) < MIN_QUERY_CHARS:
        raise ValueError(f"name_query must be at least {MIN_QUERY_CHARS} characters")
    return query, query.split(" ")


def validate_district(district: int | None) -> int | None:
    if district is not None and district not in DISTRICTS:
        raise ValueError("district must be between 1 and 28")
    return district


def parse_month(value: str, field: str) -> date:
    """Parse "YYYY-MM" or "YYYY-MM-DD" to the first of that month.

    `contract_date` is always the first of a month, so any day within a month selects it.
    """
    for fmt in ("%Y-%m-%d", "%Y-%m"):
        try:
            return datetime.strptime(value.strip(), fmt).date().replace(day=1)
        except ValueError:
            continue
    raise ValueError(f"{field} must look like 2025-03 or 2025-03-15, got {value!r}")


def _check_range(low: float | None, high: float | None, name: str) -> None:
    if low is not None and high is not None and low > high:
        raise ValueError(f"min_{name} ({low}) is greater than max_{name} ({high})")


@dataclass(frozen=True)
class TransactionFilters:
    """Validated, normalised transaction filters. Build with `from_inputs`."""

    project: str | None = None
    district: int | None = None
    type_of_sale: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    min_price: float | None = None
    max_price: float | None = None
    min_area_sqft: float | None = None
    max_area_sqft: float | None = None

    @classmethod
    def from_inputs(
        cls,
        project: str | None = None,
        district: int | None = None,
        type_of_sale: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        min_price: float | None = None,
        max_price: float | None = None,
        min_area_sqft: float | None = None,
        max_area_sqft: float | None = None,
    ) -> "TransactionFilters":
        """Validate raw tool inputs. Raises ValueError with a message fit for the caller."""
        canonical_sale = None
        if type_of_sale is not None:
            canonical_sale = TYPES_OF_SALE.get(_squash(type_of_sale))
            if canonical_sale is None:
                raise ValueError(f"type_of_sale must be one of {sorted(TYPES_OF_SALE.values())}")

        start = parse_month(start_date, "start_date") if start_date else None
        end = parse_month(end_date, "end_date") if end_date else None
        if start and end and start > end:
            raise ValueError("start_date is after end_date")
        _check_range(min_price, max_price, "price")
        _check_range(min_area_sqft, max_area_sqft, "area_sqft")

        return cls(
            project=_squash(project) if project is not None else None,
            district=validate_district(district),
            type_of_sale=canonical_sale,
            start_date=start,
            end_date=end,
            min_price=min_price,
            max_price=max_price,
            min_area_sqft=min_area_sqft,
            max_area_sqft=max_area_sqft,
        )

    def to_sql(self) -> tuple[str, dict[str, Any]]:
        """Return (WHERE clause, parameters). The clause only contains fixed fragments."""
        fragments = (
            ("project", "project = {project:String}"),
            ("district", "district = {district:UInt8}"),
            ("type_of_sale", "type_of_sale = {type_of_sale:String}"),
            ("start_date", "contract_date >= {start_date:Date}"),
            ("end_date", "contract_date <= {end_date:Date}"),
            ("min_price", "price >= {min_price:Float64}"),
            ("max_price", "price <= {max_price:Float64}"),
            ("min_area_sqft", "area_sqft >= {min_area_sqft:Float64}"),
            ("max_area_sqft", "area_sqft <= {max_area_sqft:Float64}"),
        )
        conditions: list[str] = []
        params: dict[str, Any] = {}
        for name, fragment in fragments:
            value = getattr(self, name)
            if value is None:
                continue
            conditions.append(fragment)
            params[name] = value.isoformat() if isinstance(value, date) else value
        return ("WHERE " + " AND ".join(conditions)) if conditions else "", params
