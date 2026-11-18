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

# URA property types grouped so that price per sqft is comparable inside a group. Landed
# psf is measured on land area and strata-landed on floor area, so they are kept apart.
SEGMENTS = {
    "non-landed": ("Apartment", "Condominium"),
    "ec": ("Executive Condominium",),
    "landed": ("Terrace", "Semi-detached", "Detached"),
    "strata-landed": ("Strata Terrace", "Strata Semi-detached", "Strata Detached"),
}
PERIODS = {"month": "toStartOfMonth", "quarter": "toStartOfQuarter", "year": "toStartOfYear"}

MIN_RADIUS_M = 100
MAX_RADIUS_M = 3000
MAX_MONTHS = 60
MAX_PEERS = 50
AREA_BAND = 0.2  # +/-20% around a requested size
MAX_LEASE_YEAR_TOLERANCE = 60
MIN_VALUATION_MONTHS = 6
MAX_UNIT_AREA_SQFT = 20000
MAX_FLOOR = 100
AGE_GAP_NOTE_YEARS = 15  # peers whose lease start differs this much are flagged as a different age

# Growth scan: resales a project needs before its trend is ranked
MAX_GROWTH_TRANSACTIONS = 50
GROWTH_TENURES = ("any", "freehold", "999 years", "freehold or 999 years")

# Affordability search: caps and the mortgage assumptions offered to the user
MAX_AFFORDABLE_RESULTS = 50
MIN_MORTGAGE_YEARS = 5
MAX_MORTGAGE_YEARS = 35
MIN_STRESS_RATE_PCT = 0.1
MAX_STRESS_RATE_PCT = 10.0


def _squash(text: str) -> str:
    """Upper-case and collapse whitespace, the form project names are stored in."""
    return " ".join(text.upper().split())


def normalise_project_query(name_query: str) -> tuple[str, list[str]]:
    """Normalise a project name query. Returns (query, words)."""
    query = _squash(name_query)
    if len(query) < MIN_QUERY_CHARS:
        raise ValueError(f"name_query must be at least {MIN_QUERY_CHARS} characters")
    return query, query.split(" ")


def normalise_project_name(project: str) -> str:
    """Normalise an exact project name (the form names are stored in). Must not be empty."""
    name = _squash(project)
    if not name:
        raise ValueError("project is required")
    return name


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


def resolve_segment(segment: str | None, project: str | None = None) -> str | None:
    """Return a canonical segment name, or None for no segment filter.

    "auto" means non-landed for market-wide questions, but no filter when one project is
    named (the project decides its own type).
    """
    if segment is None:
        return None
    key = _squash(segment).lower()
    if key == "all":
        return None
    if key == "auto":
        return None if project else "non-landed"
    if key not in SEGMENTS:
        raise ValueError(f"segment must be one of {[*SEGMENTS, 'all', 'auto']}")
    return key


def segment_of(property_type: str) -> tuple[str, tuple[str, ...]]:
    """The segment (name, property types) a property type belongs to.

    An unknown type compares only with itself rather than being mixed into another group.
    """
    for name, types in SEGMENTS.items():
        if property_type in types:
            return name, types
    return property_type, (property_type,)


def validate_period(period: str) -> str:
    key = period.strip().lower()
    if key not in PERIODS:
        raise ValueError(f"period must be one of {sorted(PERIODS)}")
    return key


def period_start(day: date, period: str) -> date:
    """First day of the month, quarter or year ("month", "quarter", "year") containing `day`."""
    if period == "year":
        return date(day.year, 1, 1)
    if period == "quarter":
        return date(day.year, 3 * ((day.month - 1) // 3) + 1, 1)
    return day.replace(day=1)


def add_months(day: date, months: int) -> date:
    """First of the month that is `months` after (or before, if negative) `day`."""
    index = day.year * 12 + (day.month - 1) + months
    return date(index // 12, index % 12 + 1, 1)


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
    segment: str | None = None
    property_types: tuple[str, ...] | None = None

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
        segment: str | None = None,
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

        segment_name = resolve_segment(segment, project)
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
            segment=segment_name,
            property_types=SEGMENTS[segment_name] if segment_name else None,
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
            ("property_types", "property_type IN {property_types:Array(String)}"),
        )
        conditions: list[str] = []
        params: dict[str, Any] = {}
        for name, fragment in fragments:
            value = getattr(self, name)
            if value is None:
                continue
            conditions.append(fragment)
            if isinstance(value, date):
                value = value.isoformat()
            elif isinstance(value, tuple):
                value = list(value)
            params[name] = value
        return ("WHERE " + " AND ".join(conditions)) if conditions else "", params


@dataclass(frozen=True)
class PeerFilters:
    """Conditions applied identically to the target project and its peers, so the comparison
    is like for like. Fragments are fixed text and every value is a parameter."""

    property_types: tuple[str, ...]
    start_date: date
    type_of_sale: str | None = None
    min_area_sqft: float | None = None
    max_area_sqft: float | None = None
    freehold: bool | None = None
    min_lease_year: int | None = None
    max_lease_year: int | None = None

    def to_sql(self) -> tuple[str, dict[str, Any]]:
        """Return (" AND ..." fragments, parameters) to append to an existing WHERE."""
        fragments = (
            ("property_types", "property_type IN {property_types:Array(String)}"),
            ("start_date", "contract_date >= {start_date:Date}"),
            ("type_of_sale", "type_of_sale = {type_of_sale:String}"),
            ("min_area_sqft", "area_sqft >= {min_area_sqft:Float64}"),
            ("max_area_sqft", "area_sqft <= {max_area_sqft:Float64}"),
            ("freehold", "(tenure = 'Freehold') = {freehold:UInt8}"),
            ("min_lease_year", "lease_year >= {min_lease_year:UInt16}"),
            ("max_lease_year", "lease_year <= {max_lease_year:UInt16}"),
        )
        sql: list[str] = []
        params: dict[str, Any] = {}
        for name, fragment in fragments:
            value = getattr(self, name)
            if value is None:
                continue
            sql.append(f"AND {fragment}")
            if isinstance(value, date):
                value = value.isoformat()
            elif isinstance(value, tuple):
                value = list(value)
            elif isinstance(value, bool):
                value = int(value)
            params[name] = value
        return " ".join(sql), params


@dataclass(frozen=True)
class NearbyQuery:
    """Validated inputs for comparing one project with the projects around it."""

    project: str
    radius_m: int = 1000
    months: int = 12
    type_of_sale: str | None = None
    area_sqft: float | None = None
    same_tenure_type: bool = False
    max_peers: int = 10
    lease_year_tolerance: int | None = None

    @classmethod
    def from_inputs(
        cls,
        project: str,
        radius_m: int = 1000,
        months: int = 12,
        type_of_sale: str | None = None,
        area_sqft: float | None = None,
        same_tenure_type: bool = False,
        max_peers: int = 10,
        lease_year_tolerance: int | None = None,
    ) -> "NearbyQuery":
        if not _squash(project):
            raise ValueError("project is required")
        if not MIN_RADIUS_M <= radius_m <= MAX_RADIUS_M:
            raise ValueError(f"radius_m must be between {MIN_RADIUS_M} and {MAX_RADIUS_M}")
        if not 1 <= months <= MAX_MONTHS:
            raise ValueError(f"months must be between 1 and {MAX_MONTHS}")
        if not 1 <= max_peers <= MAX_PEERS:
            raise ValueError(f"max_peers must be between 1 and {MAX_PEERS}")
        if area_sqft is not None and area_sqft <= 0:
            raise ValueError("area_sqft must be positive")
        if (
            lease_year_tolerance is not None
            and not 0 <= lease_year_tolerance <= MAX_LEASE_YEAR_TOLERANCE
        ):
            raise ValueError(
                f"lease_year_tolerance must be between 0 and {MAX_LEASE_YEAR_TOLERANCE}"
            )
        sale = None
        if type_of_sale is not None:
            sale = TYPES_OF_SALE.get(_squash(type_of_sale))
            if sale is None:
                raise ValueError(f"type_of_sale must be one of {sorted(TYPES_OF_SALE.values())}")
        return cls(
            _squash(project),
            radius_m,
            months,
            sale,
            area_sqft,
            same_tenure_type,
            max_peers,
            lease_year_tolerance,
        )

    def size_band(self) -> tuple[float, float] | tuple[None, None]:
        """The +/-20% size window around `area_sqft`, if one was given."""
        if self.area_sqft is None:
            return None, None
        return self.area_sqft * (1 - AREA_BAND), self.area_sqft * (1 + AREA_BAND)


@dataclass(frozen=True)
class ValuationQuery:
    """Validated inputs for estimating the value of one unit in a project."""

    project: str
    area_sqft: float
    floor: int | None = None
    type_of_sale: str | None = None
    radius_m: int = 1500
    months: int = 24
    max_peers: int = 12

    @classmethod
    def from_inputs(
        cls,
        project: str,
        area_sqft: float,
        floor: int | None = None,
        type_of_sale: str | None = None,
        radius_m: int = 1500,
        months: int = 24,
        max_peers: int = 12,
    ) -> "ValuationQuery":
        if not _squash(project):
            raise ValueError("project is required")
        if not 0 < area_sqft <= MAX_UNIT_AREA_SQFT:
            raise ValueError(f"area_sqft must be between 0 and {MAX_UNIT_AREA_SQFT}")
        if floor is not None and not 1 <= floor <= MAX_FLOOR:
            raise ValueError(f"floor must be between 1 and {MAX_FLOOR}")
        if not MIN_RADIUS_M <= radius_m <= MAX_RADIUS_M:
            raise ValueError(f"radius_m must be between {MIN_RADIUS_M} and {MAX_RADIUS_M}")
        if not MIN_VALUATION_MONTHS <= months <= MAX_MONTHS:
            raise ValueError(f"months must be between {MIN_VALUATION_MONTHS} and {MAX_MONTHS}")
        if not 1 <= max_peers <= MAX_PEERS:
            raise ValueError(f"max_peers must be between 1 and {MAX_PEERS}")
        sale = None
        if type_of_sale is not None:
            sale = TYPES_OF_SALE.get(_squash(type_of_sale))
            if sale is None:
                raise ValueError(f"type_of_sale must be one of {sorted(TYPES_OF_SALE.values())}")
        return cls(_squash(project), area_sqft, floor, sale, radius_m, months, max_peers)


@dataclass(frozen=True)
class GrowthQuery:
    """Validated inputs for ranking nearby projects by their psf growth."""

    project: str
    radius_m: int = 2000
    months: int = 60
    max_peers: int = 50
    min_transactions: int = 4
    min_area_sqft: float | None = None
    max_area_sqft: float | None = None
    segment: str = "non-landed"
    tenure: str = "any"
    include_rental: bool = False

    @classmethod
    def from_inputs(
        cls,
        project: str,
        radius_m: int = 2000,
        months: int = 60,
        max_peers: int = 50,
        min_transactions: int = 4,
        min_area_sqft: float | None = None,
        max_area_sqft: float | None = None,
        segment: str = "non-landed",
        tenure: str = "any",
        include_rental: bool = False,
    ) -> "GrowthQuery":
        if not _squash(project):
            raise ValueError("project is required")
        if not MIN_RADIUS_M <= radius_m <= MAX_RADIUS_M:
            raise ValueError(f"radius_m must be between {MIN_RADIUS_M} and {MAX_RADIUS_M}")
        if not 1 <= months <= MAX_MONTHS:
            raise ValueError(f"months must be between 1 and {MAX_MONTHS}")
        if not 1 <= max_peers <= MAX_PEERS:
            raise ValueError(f"max_peers must be between 1 and {MAX_PEERS}")
        if not 1 <= min_transactions <= MAX_GROWTH_TRANSACTIONS:
            raise ValueError(f"min_transactions must be between 1 and {MAX_GROWTH_TRANSACTIONS}")
        if min_area_sqft is not None and min_area_sqft <= 0:
            raise ValueError("min_area_sqft must be positive")
        if max_area_sqft is not None and max_area_sqft <= 0:
            raise ValueError("max_area_sqft must be positive")
        _check_range(min_area_sqft, max_area_sqft, "area_sqft")
        segment_name = resolve_segment(segment, None)
        if segment_name is None:
            raise ValueError("segment must be one of non-landed, ec, landed or strata-landed")
        tenure_key = _squash(tenure).lower()
        if tenure_key not in GROWTH_TENURES:
            raise ValueError(f"tenure must be one of {list(GROWTH_TENURES)}")
        return cls(
            _squash(project),
            radius_m,
            months,
            max_peers,
            min_transactions,
            min_area_sqft,
            max_area_sqft,
            segment_name,
            tenure_key,
            bool(include_rental),
        )


@dataclass(frozen=True)
class AffordabilityQuery:
    """Validated inputs for finding projects whose sales fit a budget."""

    max_price: float
    district: int | None = None
    segment: str = "non-landed"
    min_area_sqft: float | None = None
    min_transactions: int = 3
    limit: int = 20

    @classmethod
    def from_inputs(
        cls,
        max_price: float,
        district: int | None = None,
        segment: str = "non-landed",
        min_area_sqft: float | None = None,
        min_transactions: int = 3,
        limit: int = 20,
    ) -> "AffordabilityQuery":
        if not 0 < max_price <= 100_000_000:
            raise ValueError("max_price must be between 0 and 100,000,000 SGD")
        if min_area_sqft is not None and min_area_sqft <= 0:
            raise ValueError("min_area_sqft must be positive")
        if not 1 <= min_transactions <= MAX_GROWTH_TRANSACTIONS:
            raise ValueError(f"min_transactions must be between 1 and {MAX_GROWTH_TRANSACTIONS}")
        segment_name = resolve_segment(segment, None)
        if segment_name is None:
            raise ValueError("segment must be one of non-landed, ec, landed or strata-landed")
        return cls(
            max_price,
            validate_district(district),
            segment_name,
            min_area_sqft,
            min_transactions,
            max(1, min(limit, MAX_AFFORDABLE_RESULTS)),
        )
