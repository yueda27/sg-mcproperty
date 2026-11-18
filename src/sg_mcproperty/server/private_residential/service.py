"""Use cases for private residential data: validate, apply limits, shape results.

Calls the repository for rows and returns typed, JSON friendly results. Knows nothing about
SQL or MCP.
"""

import math
import re
from dataclasses import replace
from datetime import date, datetime
from statistics import median
from typing import Any, TypedDict

from . import regression
from .filters import (
    AGE_GAP_NOTE_YEARS,
    MAX_MORTGAGE_YEARS,
    MAX_STRESS_RATE_PCT,
    MIN_MORTGAGE_YEARS,
    MIN_STRESS_RATE_PCT,
    PERIODS,
    SEGMENTS,
    AffordabilityQuery,
    GrowthQuery,
    NearbyQuery,
    PeerFilters,
    TransactionFilters,
    ValuationQuery,
    add_months,
    normalise_project_name,
    normalise_project_query,
    period_start,
    segment_of,
    validate_district,
    validate_period,
)
from .repository import PrivateResidentialRepository

MAX_PROJECT_RESULTS = 25
MAX_TRANSACTION_RESULTS = 100
LOW_SAMPLE = 5  # fewer transactions than this is flagged as a thin sample
MIN_PEER_TRANSACTIONS = 3  # a peer project needs this many matching sales to count
SUMMARY_WINDOW_MONTHS = 12  # project_summary compares the latest 12 months with the 12 before

# estimate_value
MAX_SAMPLE_ROWS = 20_000
MIN_OBSERVATIONS = 30  # fewer comparable sales than this and no model is fitted
SMALL_SAMPLE = 60
WEAK_R_SQUARED = 0.5
HIGH_ERROR_PCT = 10.0
MIN_TARGET_SALES = 5  # own sales needed to value at the project's own price level
SAMPLE_AREA_RANGE = (0.5, 2.0)  # compare units from half to double the requested size
FLOOR_SEGMENTS = ("non-landed", "ec")  # landed property has no floor
LEASE_CAP_YEARS = 99
LONG_LEASE_YEARS = 900  # tenures this long behave like freehold
WIDE_INTERVAL_RATIO = 1.5
COMPARABLE_AREA_BAND = 0.2

# market_activity: periods compared (recent vs the same number before) and months per period
ACTIVITY_WINDOW = {"month": 3, "quarter": 2, "year": 1}
PERIOD_MONTHS = {"month": 1, "quarter": 3, "year": 12}

# affordability_search: illustrative borrowing capacity, not a loan offer
TDSR_PCT = 55.0  # share of gross monthly income a lender may count towards repayments
MIN_DOWN_PAYMENT_PCT = 0.0
MAX_DOWN_PAYMENT_PCT = 90.0


class ProjectMatch(TypedDict):
    project: str
    street: str
    district: int
    tenure: str
    lease_year: int | None
    transactions: int
    first_sale: str
    last_sale: str
    match_type: str


class Transaction(TypedDict):
    project: str
    street: str
    district: int
    contract_date: str
    type_of_sale: str
    price: float
    area_sqft: float
    psf: float | None
    floor: int | None
    tenure: str
    lease_year: int | None


class TransactionPage(TypedDict):
    total_matches: int
    returned: int
    transactions: list[Transaction]


class TrendPoint(TypedDict):
    period: str
    transactions: int
    median_price: float | None
    median_psf: float | None
    low_sample: bool


class PriceTrend(TypedDict):
    period: str
    segment: str | None
    points: list[TrendPoint]
    psf_change_pct: float | None
    notes: list[str]


class PsfStats(TypedDict):
    transactions: int
    median_psf: float | None
    p25_psf: float | None
    p75_psf: float | None
    median_price: float | None


class PeerGroupStats(PsfStats):
    projects: int


class TargetProject(TypedDict):
    street: str
    district: int
    property_type: str
    tenure: str
    lease_year: int | None
    stats: PsfStats | None


class PeerProject(TypedDict):
    project: str
    street: str
    district: int
    property_type: str
    tenure: str
    lease_year: int | None
    transactions: int
    median_psf: float | None
    median_price: float | None
    distance_m: int | None


class IndicativeValue(TypedDict):
    area_sqft: float
    low: int
    mid: int
    high: int


class NearbyComparison(TypedDict):
    project: str
    segment: str
    basis: str
    radius_m: int | None
    window_start: str
    window_end: str
    target: TargetProject
    peers: list[PeerProject]
    peer_group: PeerGroupStats | None
    premium_pct: float | None
    indicative_value: IndicativeValue | None
    notes: list[str]


class SegmentInfo(TypedDict):
    segment: str
    property_types: list[str]
    transactions: int


class CoordinateCoverage(TypedDict):
    projects_pct: float | None
    transactions_pct: float | None


class DatasetInfo(TypedDict):
    transactions: int
    projects: int
    first_month: str | None
    last_month: str | None
    districts: list[int]
    types_of_sale: list[str]
    segments: list[SegmentInfo]
    coordinate_coverage: CoordinateCoverage
    notes: list[str]


class SalesMix(TypedDict):
    new_sale: int
    sub_sale: int
    resale: int


class ValueRange(TypedDict):
    min: float | None
    median: float | None
    max: float | None


class PsfDistribution(TypedDict):
    p25: float | None
    median: float | None
    p75: float | None


class RecentVersusPrior(TypedDict):
    recent_start: str
    prior_start: str
    recent_transactions: int
    recent_median_psf: float | None
    prior_transactions: int
    prior_median_psf: float | None
    change_pct: float | None


class ProjectSummary(TypedDict):
    project: str
    street: str
    district: int
    property_type: str
    segment: str
    tenure: str
    lease_year: int | None
    has_coordinates: bool
    transactions: int
    first_sale: str
    last_sale: str
    sales_mix: SalesMix
    price: ValueRange
    psf: PsfDistribution
    area_sqft: ValueRange
    last_12_months: RecentVersusPrior
    notes: list[str]


class ValuationUnit(TypedDict):
    area_sqft: float
    floor: int | None
    type_of_sale: str
    remaining_lease_years: float | None
    freehold: bool


class ValueBand(TypedDict):
    median: int
    low: int
    high: int
    interval_pct: int
    psf: float


class ModelStats(TypedDict):
    mode: str
    sales_used: int
    projects: int
    r_squared: float
    typical_error_pct: float
    outliers_removed: int
    features: list[str]


class ValueEffects(TypedDict):
    size_plus_10pct_area_pct: float | None
    per_10_floors_pct: float | None
    per_10_years_lease_pct: float | None
    freehold_premium_pct: float | None
    new_sale_premium_pct: float | None
    monthly_trend_pct: float | None
    target_project_premium_pct: float | None


class ComparableCheck(TypedDict):
    source: str
    sales_used: int
    median_psf: float | None
    implied_value: int | None


class ActivitySnapshot(TypedDict):
    sales_in_sample: int
    last_3_months: int
    prior_3_months: int
    volume_change_pct: float | None


class ValueEstimate(TypedDict):
    project: str
    segment: str
    basis: str
    radius_m: int | None
    window_start: str
    window_end: str
    unit: ValuationUnit
    estimate: ValueBand | None
    model: ModelStats | None
    effects: ValueEffects | None
    comparable_check: ComparableCheck | None
    activity: ActivitySnapshot
    peers: list[PeerProject]
    notes: list[str]


class ActivityPoint(TypedDict):
    period: str
    transactions: int
    new_sales: int
    sub_sales: int
    resales: int
    median_price: float | None
    median_psf: float | None


class MarketActivity(TypedDict):
    period: str
    segment: str | None
    points: list[ActivityPoint]
    total_transactions: int
    average_per_period: float | None
    recent_periods: int
    recent_transactions: int
    prior_transactions: int
    volume_change_pct: float | None
    notes: list[str]


class Coverage(TypedDict):
    transactions: int
    projects: int
    first_month: str | None
    last_month: str | None


class GrowthStats(TypedDict):
    project: str
    street: str
    district: int
    tenure: str
    lease_year: int | None
    transactions: int
    median_psf: float | None
    median_price: float | None
    annual_growth_pct: float | None


class GrowthPeer(GrowthStats):
    distance_m: int
    better_than_target: bool


class SizeBand(TypedDict):
    min_area_sqft: float | None
    max_area_sqft: float | None


class NearbyGrowth(TypedDict):
    project: str
    segment: str
    radius_m: int
    window_start: str
    window_end: str
    size_band: SizeBand | None
    tenure_filter: str
    target: GrowthStats
    peers: list[GrowthPeer]
    projects_compared: int
    insufficient_data: int
    notes: list[str]


class AffordableProject(TypedDict):
    project: str
    street: str
    district: int
    transactions: int
    median_price: float | None
    min_price: float | None
    median_psf: float | None
    min_area_sqft: float | None
    max_area_sqft: float | None


class MortgageAssumptions(TypedDict):
    monthly_income: float
    down_payment_pct: float
    loan_tenure_years: int
    stress_rate_pct: float
    tdsr_pct: float


class AffordabilityFilters(TypedDict):
    district: int | None
    segment: str
    min_area_sqft: float | None


class AffordabilityResult(TypedDict):
    max_price: float
    assumptions: MortgageAssumptions | None
    filters: AffordabilityFilters
    projects: list[AffordableProject]
    notes: list[str]


def _iso(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value


def _num(value: Any) -> Any:
    """None for missing or NaN numbers (ClickHouse returns NaN for aggregates over no rows)."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    return value


def _psf_stats(row: dict[str, Any]) -> PsfStats | None:
    if not row["transactions"]:
        return None
    return {
        "transactions": row["transactions"],
        "median_psf": _num(row["median_psf"]),
        "p25_psf": _num(row["p25_psf"]),
        "p75_psf": _num(row["p75_psf"]),
        "median_price": _num(row["median_price"]),
    }


def _tenure_years(tenure: str) -> int | None:
    match = re.match(r"\s*(\d+)\s*years?", tenure)
    return int(match.group(1)) if match else None


def _is_freehold(tenure: str) -> bool:
    years = _tenure_years(tenure)
    return tenure == "Freehold" or (years is not None and years >= LONG_LEASE_YEARS)


def _remaining_lease(tenure: str, lease_year: int | None, year: int) -> float:
    """Years of lease left in `year`, capped: freehold and unknown leases count as the cap."""
    years = _tenure_years(tenure)
    if _is_freehold(tenure) or years is None or lease_year is None:
        return float(LEASE_CAP_YEARS)
    return float(min(max(years - (year - lease_year), 0), LEASE_CAP_YEARS))


def _pct(coefficient: float | None, scale: float = 1.0) -> float | None:
    """A log-price coefficient as a percentage effect: exp(b * scale) - 1."""
    return None if coefficient is None else round((math.exp(coefficient * scale) - 1) * 100, 1)


def max_affordable_price(
    monthly_income: float,
    down_payment_pct: float,
    loan_tenure_years: int,
    stress_rate_pct: float,
) -> float:
    """Illustrative maximum purchase price from income, before duties and fees.

    Counts `TDSR_PCT` of gross monthly income towards the repayment, prices the loan at the
    stress rate (not a promotional rate), then grosses up for the down payment. It ignores
    existing debts, stamp duty, legal fees and renovation, so it is a budgeting aid, not a
    loan offer.
    """
    if monthly_income <= 0:
        raise ValueError("monthly_income must be positive")
    if not MIN_DOWN_PAYMENT_PCT <= down_payment_pct <= MAX_DOWN_PAYMENT_PCT:
        raise ValueError(
            f"down_payment_pct must be between {MIN_DOWN_PAYMENT_PCT:g} and "
            f"{MAX_DOWN_PAYMENT_PCT:g}"
        )
    if not MIN_MORTGAGE_YEARS <= loan_tenure_years <= MAX_MORTGAGE_YEARS:
        raise ValueError(
            f"loan_tenure_years must be between {MIN_MORTGAGE_YEARS} and {MAX_MORTGAGE_YEARS}"
        )
    if not MIN_STRESS_RATE_PCT <= stress_rate_pct <= MAX_STRESS_RATE_PCT:
        raise ValueError(
            f"stress_rate_pct must be between {MIN_STRESS_RATE_PCT:g} and {MAX_STRESS_RATE_PCT:g}"
        )
    monthly_rate = stress_rate_pct / 100 / 12
    months = loan_tenure_years * 12
    payment = monthly_income * TDSR_PCT / 100
    loan = payment * (1 - (1 + monthly_rate) ** -months) / monthly_rate
    return round(loan / (1 - down_payment_pct / 100), -3)


def _growth_row(row: dict[str, Any]) -> GrowthStats:
    growth = _num(row.get("annual_growth"))
    return {
        "project": row["project"],
        "street": row["street"],
        "district": row["district"],
        "tenure": row["tenure"],
        "lease_year": _num(row.get("lease_year")),
        "transactions": row["transactions"] or 0,
        "median_psf": _num(row.get("median_psf")),
        "median_price": _num(row.get("median_price")),
        "annual_growth_pct": None if growth is None else round(growth * 100, 1),
    }


def _sale_from_row(
    row: dict[str, Any], target: str, start: date, use_floor: bool
) -> regression.Sale | None:
    if use_floor and row["floor"] is None:
        return None
    sold = row["contract_date"]
    lease_year = _num(row["lease_year"])
    return regression.Sale(
        area_sqft=row["area_sqft"],
        floor=row["floor"],
        month_index=(sold.year - start.year) * 12 + (sold.month - start.month),
        new_sale=row["type_of_sale"] == "New Sale",
        freehold=_is_freehold(row["tenure"]),
        remaining_lease=_remaining_lease(row["tenure"], lease_year, sold.year),
        is_target=row["project"] == target,
        price=row["price"],
        project=row["project"],
    )


def _clamp(limit: int, maximum: int) -> int:
    return max(1, min(limit, maximum))


class PrivateResidentialService:
    """The use cases the MCP layer calls: validate, apply limits, shape results."""

    def __init__(self, repository: PrivateResidentialRepository) -> None:
        self._repository = repository

    def search_projects(
        self, name_query: str, district: int | None = None, limit: int = 10
    ) -> list[ProjectMatch]:
        query, tokens = normalise_project_query(name_query)
        district = validate_district(district)
        limit = _clamp(limit, MAX_PROJECT_RESULTS)
        rows = self._repository.find_projects(query, tokens, district, limit)
        if not rows:
            rows = self._repository.find_projects_fuzzy(query, tokens, district, limit)
        return [self._project(row) for row in rows]

    def search_transactions(self, filters: TransactionFilters, limit: int = 20) -> TransactionPage:
        total = self._repository.count_transactions(filters)
        rows = self._repository.list_transactions(filters, _clamp(limit, MAX_TRANSACTION_RESULTS))
        return {
            "total_matches": total,
            "returned": len(rows),
            "transactions": [self._transaction(row) for row in rows],
        }

    def price_trend(self, filters: TransactionFilters, period: str = "quarter") -> PriceTrend:
        key = validate_period(period)
        rows = self._repository.price_trend(filters, PERIODS[key])
        points: list[TrendPoint] = [
            {
                "period": _iso(row["period"]),
                "transactions": row["transactions"],
                "median_price": _num(row["median_price"]),
                "median_psf": _num(row["median_psf"]),
                "low_sample": row["transactions"] < LOW_SAMPLE,
            }
            for row in rows
        ]
        solid = [p for p in points if not p["low_sample"] and p["median_psf"]]
        change = None
        if len(solid) >= 2:
            change = round((solid[-1]["median_psf"] / solid[0]["median_psf"] - 1) * 100, 1)

        notes: list[str] = []
        if not points:
            notes.append("No transactions match these filters.")
        if filters.segment is None and filters.project is None:
            notes.append("No segment filter: landed and non-landed psf are mixed.")
        if any(p["low_sample"] for p in points):
            notes.append(f"Periods marked low_sample have fewer than {LOW_SAMPLE} transactions.")
        return {
            "period": key,
            "segment": filters.segment,
            "points": points,
            "psf_change_pct": change,
            "notes": notes,
        }

    def compare_nearby(self, query: NearbyQuery) -> NearbyComparison:
        repo = self._repository
        profile = self._require_profile(query.project)
        segment, property_types = segment_of(profile["property_type"])
        window_end = repo.coverage()["last_month"].date()
        low, high = query.size_band()
        target_lease_year = _num(profile["lease_year"])
        tolerance = query.lease_year_tolerance if target_lease_year is not None else None
        peer_filters = PeerFilters(
            property_types=property_types,
            start_date=add_months(window_end, -(query.months - 1)),
            type_of_sale=query.type_of_sale,
            min_area_sqft=low,
            max_area_sqft=high,
            freehold=(profile["tenure"] == "Freehold") if query.same_tenure_type else None,
            min_lease_year=target_lease_year - tolerance if tolerance is not None else None,
            max_lease_year=target_lease_year + tolerance if tolerance is not None else None,
        )

        basis, peer_rows = self._find_peers(
            query.project, profile, query.radius_m, peer_filters, query.max_peers
        )

        target_filters = replace(
            peer_filters, freehold=None, min_lease_year=None, max_lease_year=None
        )
        target = _psf_stats(repo.project_stats(query.project, target_filters))
        pooled = (
            repo.pooled_stats([p["project"] for p in peer_rows], peer_filters)
            if peer_rows
            else None
        )
        group_stats = _psf_stats(pooled) if pooled else None
        peer_group: PeerGroupStats | None = (
            {**group_stats, "projects": pooled["projects"]} if group_stats else None
        )

        premium = None
        if target and peer_group and target["median_psf"] and peer_group["median_psf"]:
            premium = round((target["median_psf"] / peer_group["median_psf"] - 1) * 100, 1)
        indicative = None
        if query.area_sqft and peer_group and peer_group["median_psf"]:
            area = query.area_sqft
            indicative = {
                "area_sqft": area,
                "low": round((peer_group["p25_psf"] or peer_group["median_psf"]) * area),
                "mid": round(peer_group["median_psf"] * area),
                "high": round((peer_group["p75_psf"] or peer_group["median_psf"]) * area),
            }

        peers = self._peer_projects(peer_rows)
        notes = self._nearby_notes(query, profile, basis, target, peer_rows, peer_group)
        return {
            "project": query.project,
            "segment": segment,
            "basis": basis,
            "radius_m": query.radius_m if basis == "radius" else None,
            "window_start": peer_filters.start_date.isoformat(),
            "window_end": window_end.isoformat(),
            "target": {
                "street": profile["street"],
                "district": profile["district"],
                "property_type": profile["property_type"],
                "tenure": profile["tenure"],
                "lease_year": target_lease_year,
                "stats": target,
            },
            "peers": peers,
            "peer_group": peer_group,
            "premium_pct": premium,
            "indicative_value": indicative,
            "notes": notes,
        }

    @staticmethod
    def _nearby_notes(
        query: NearbyQuery,
        profile: dict[str, Any],
        basis: str,
        target: PsfStats | None,
        peer_rows: list[dict[str, Any]],
        peer_group: PeerGroupStats | None,
    ) -> list[str]:
        notes = ["Indicative comparison of recorded transaction prices, not a formal valuation."]
        if basis != "radius":
            notes.append(
                f"This project has no coordinates in the data, so peers are the busiest projects "
                f"in the same {basis} rather than the nearest ones."
            )
        if target is None:
            notes.append("The project has no matching transactions in the window.")
        elif target["transactions"] < LOW_SAMPLE:
            notes.append(
                f"Only {target['transactions']} matching sales for the project: unreliable."
            )
        if not peer_rows:
            notes.append(
                f"No peer has {MIN_PEER_TRANSACTIONS}+ matching sales: try a larger radius_m, "
                "more months, or drop the size and sale type filters."
            )
        elif peer_group and peer_group["projects"] < 3:
            notes.append(
                f"Only {peer_group['projects']} peer projects: treat the peer median with care."
            )
        if query.lease_year_tolerance is not None and _num(profile["lease_year"]) is None:
            notes.append("lease_year_tolerance ignored: the project is freehold (no lease year).")
        elif peer_rows and query.lease_year_tolerance is None:
            gap = (
                sum(
                    row["lease_year"] is not None
                    and abs(row["lease_year"] - profile["lease_year"]) > AGE_GAP_NOTE_YEARS
                    for row in peer_rows
                )
                if profile["lease_year"] is not None
                else 0
            )
            if gap:
                notes.append(
                    f"{gap} of {len(peer_rows)} peers have a lease start more than "
                    f"{AGE_GAP_NOTE_YEARS} years from the project's ({profile['lease_year']}): "
                    "older projects usually price lower, set lease_year_tolerance (e.g. 10) "
                    "to compare projects of similar age."
                )
        if peer_rows and not query.same_tenure_type:
            is_freehold = profile["tenure"] == "Freehold"
            other = sum((row["tenure"] == "Freehold") != is_freehold for row in peer_rows)
            if other:
                notes.append(
                    f"{other} of {len(peer_rows)} peers have a different tenure type "
                    "(freehold vs leasehold): set same_tenure_type=true for a stricter match."
                )
        return notes

    def _require_profile(self, project: str) -> dict[str, Any]:
        profile = self._repository.project_profile(project)
        if profile is None:
            raise ValueError(
                f"Unknown project {project!r}: use search_projects to find the exact name"
            )
        return profile

    def _find_peers(
        self,
        project: str,
        profile: dict[str, Any],
        radius_m: int,
        peer_filters: PeerFilters,
        max_peers: int,
    ) -> tuple[str, list[dict[str, Any]]]:
        """Peer projects and how they were found: "radius", else "street", else "district"."""
        repo = self._repository
        x, y = _num(profile["x"]), _num(profile["y"])
        if x is not None and y is not None:
            peers = repo.find_peers_by_distance(
                project, x, y, radius_m, peer_filters, MIN_PEER_TRANSACTIONS, max_peers
            )
            return "radius", peers
        # No coordinates (common for new launches): same street is the closest proxy, then
        # the same district.
        peers = repo.find_peers_by_area(
            project, "street", profile["street"], peer_filters, MIN_PEER_TRANSACTIONS, max_peers
        )
        if peers:
            return "street", peers
        peers = repo.find_peers_by_area(
            project, "district", profile["district"], peer_filters, MIN_PEER_TRANSACTIONS, max_peers
        )
        return "district", peers

    @staticmethod
    def _peer_projects(peer_rows: list[dict[str, Any]]) -> list[PeerProject]:
        return [
            {**{k: _num(v) for k, v in row.items()}, "distance_m": _num(row["distance_m"])}
            for row in peer_rows
        ]  # type: ignore[misc]

    def estimate_value(self, query: ValuationQuery) -> ValueEstimate:
        repo = self._repository
        profile = self._require_profile(query.project)
        segment, property_types = segment_of(profile["property_type"])
        window_end = repo.coverage()["last_month"].date()
        start = add_months(window_end, -(query.months - 1))
        filters = PeerFilters(
            property_types=property_types,
            start_date=start,
            min_area_sqft=query.area_sqft * SAMPLE_AREA_RANGE[0],
            max_area_sqft=query.area_sqft * SAMPLE_AREA_RANGE[1],
        )
        basis, peer_rows = self._find_peers(
            query.project, profile, query.radius_m, filters, query.max_peers
        )
        rows = repo.valuation_sample(
            [query.project, *(p["project"] for p in peer_rows)], filters, MAX_SAMPLE_ROWS
        )
        use_floor = segment in FLOOR_SEGMENTS
        sales = [
            sale
            for row in rows
            if (sale := _sale_from_row(row, query.project, start, use_floor)) is not None
        ]

        freehold = _is_freehold(profile["tenure"])
        remaining = (
            float(LEASE_CAP_YEARS)
            if freehold
            else _remaining_lease(profile["tenure"], _num(profile["lease_year"]), window_end.year)
        )
        own_floors = [s.floor for s in sales if s.is_target and s.floor is not None]
        sample_floors = [s.floor for s in sales if s.floor is not None]
        floor = query.floor
        if use_floor and floor is None and (own_floors or sample_floors):
            floor = round(median(own_floors or sample_floors))
        sale_type = query.type_of_sale or "Resale"
        unit = regression.Unit(
            area_sqft=query.area_sqft,
            floor=floor if use_floor else None,
            month_index=query.months - 1,  # value at the latest month of data
            new_sale=sale_type == "New Sale",
            freehold=freehold,
            remaining_lease=remaining,
            is_target=True,
        )

        own_sales = sum(s.is_target for s in sales)
        model = (
            regression.fit(sales, use_floor, project_effects=own_sales >= MIN_TARGET_SALES)
            if len(sales) >= MIN_OBSERVATIONS
            else None
        )
        band = effects = model_stats = None
        if model is not None:
            log_price, se = regression.predict(model, unit, use_floor)
            log_low, log_high = regression.interval(log_price, se)
            low, mid, high = math.exp(log_low), math.exp(log_price), math.exp(log_high)
            band = {
                "median": round(mid, -3),
                "low": round(low, -3),
                "high": round(high, -3),
                "interval_pct": 80,
                "psf": round(mid / query.area_sqft),
            }
            coefficient = model.coefficient
            effects = {
                "size_plus_10pct_area_pct": _pct(coefficient("ln_area"), math.log(1.1)),
                "per_10_floors_pct": _pct(coefficient("floor_10")),
                "per_10_years_lease_pct": _pct(coefficient("lease_10")),
                "freehold_premium_pct": _pct(coefficient("freehold")),
                "new_sale_premium_pct": _pct(coefficient("new_sale")),
                "monthly_trend_pct": _pct(coefficient("month")),
                "target_project_premium_pct": self._project_premium(model, sales),
            }
            model_stats = {
                "mode": model.mode,
                "sales_used": model.observations,
                "projects": len({row["project"] for row in rows}),
                "r_squared": round(model.r_squared, 3),
                "typical_error_pct": round((math.exp(model.residual_sd) - 1) * 100, 1),
                "outliers_removed": model.outliers_removed,
                "features": [n for n in model.names[1:] if not n.startswith("project:")]
                + (["project_levels"] if model.mode == "project" else []),
            }

        in_band = [
            s for s in sales if abs(s.area_sqft / query.area_sqft - 1) <= COMPARABLE_AREA_BAND
        ]
        own_in_band = [s for s in in_band if s.is_target]
        source, pool = (
            ("project", own_in_band) if len(own_in_band) >= LOW_SAMPLE else ("nearby", in_band)
        )
        check = None
        if pool:
            psf = median(s.price / s.area_sqft for s in pool)
            check = {
                "source": source,
                "sales_used": len(pool),
                "median_psf": round(psf),
                "implied_value": round(psf * query.area_sqft, -3)
                if len(pool) >= LOW_SAMPLE
                else None,
            }

        # The latest month is probably incomplete, so use the complete months before it.
        last = query.months - 1
        recent = sum(last - 3 <= s.month_index < last for s in sales)
        prior = sum(last - 6 <= s.month_index < last - 3 for s in sales)
        activity: ActivitySnapshot = {
            "sales_in_sample": len(sales),
            "last_3_months": recent,
            "prior_3_months": prior,
            "volume_change_pct": round((recent / prior - 1) * 100, 1) if prior else None,
        }

        notes = self._valuation_notes(
            query, basis, sale_type, sales, model, band, rows, unit, use_floor
        )
        return {
            "project": query.project,
            "segment": segment,
            "basis": basis,
            "radius_m": query.radius_m if basis == "radius" else None,
            "window_start": start.isoformat(),
            "window_end": window_end.isoformat(),
            "unit": {
                "area_sqft": query.area_sqft,
                "floor": unit.floor,
                "type_of_sale": sale_type,
                "remaining_lease_years": None if freehold else remaining,
                "freehold": freehold,
            },
            "estimate": band,
            "model": model_stats,
            "effects": effects,
            "comparable_check": check,
            "activity": activity,
            "peers": self._peer_projects(peer_rows),
            "notes": notes,
        }

    @staticmethod
    def _valuation_notes(
        query: ValuationQuery,
        basis: str,
        sale_type: str,
        sales: list[regression.Sale],
        model: regression.Model | None,
        band: ValueBand | None,
        rows: list[dict[str, Any]],
        unit: regression.Unit,
        use_floor: bool,
    ) -> list[str]:
        notes = [
            (
                "Statistical estimate from recorded transaction prices, not a formal valuation: "
                "it ignores view, facing, renovation and the exact unit."
            )
        ]
        if query.type_of_sale is None:
            notes.append(
                'Valued as a resale unit. Pass type_of_sale="New Sale" for new-launch pricing.'
            )
        if basis != "radius":
            notes.append(
                f"This project has no coordinates in the data, so comparables come from the same "
                f"{basis} rather than the nearest projects."
            )
        if model is None:
            notes.append(
                f"Only {len(sales)} comparable sales (at least {MIN_OBSERVATIONS} needed) so no "
                "estimate was made: try a larger radius_m, more months or a different unit size."
            )
            return notes
        if model.observations < SMALL_SAMPLE:
            notes.append(f"Small sample ({model.observations} sales): treat the range with care.")
        if model.r_squared < WEAK_R_SQUARED:
            notes.append(
                f"Weak fit (R-squared {model.r_squared:.2f}): prices vary a lot for reasons the "
                "model does not capture."
            )
        if model.mode == "area":
            own = sum(s.is_target for s in sales)
            has = "no sales" if own == 0 else f"only {own} sales"
            notes.append(
                f"The project has {has} in the window (at least {MIN_TARGET_SALES} "
                "needed), so the estimate reflects similar nearby projects, not this project's "
                "own price level. The lease and freehold effects are compared across projects "
                "and can be confounded with project quality: treat them as indicative."
            )
        areas = [s.area_sqft for s in sales]
        if not min(areas) <= query.area_sqft <= max(areas):
            notes.append("The requested size is outside the sizes sold: the estimate extrapolates.")
        floors = [s.floor for s in sales if s.floor is not None]
        if (
            use_floor
            and unit.floor is not None
            and floors
            and not min(floors) <= unit.floor <= max(floors)
        ):
            notes.append("The floor is outside the floors sold: the floor effect extrapolates.")
        typical_error = (math.exp(model.residual_sd) - 1) * 100
        if typical_error > HIGH_ERROR_PCT:
            notes.append(
                f"Prices in the sample vary by about {typical_error:.0f}% around the model: "
                "the estimate is rough."
            )
        if band and band["high"] / band["low"] > WIDE_INTERVAL_RATIO:
            notes.append("The range is wide: individual sales vary a lot around the estimate.")
        if model.outliers_removed:
            notes.append(f"{model.outliers_removed} unusual sales were excluded from the fit.")
        if len(rows) >= MAX_SAMPLE_ROWS:
            notes.append("The sample hit its size cap: only the most recent sales were used.")
        return notes

    @staticmethod
    def _project_premium(model: regression.Model, sales: list[regression.Sale]) -> float | None:
        """The target's price level versus its peers, in percent (project mode only).

        Each peer's level is relative to the target, so the target's premium is the negative of
        the sales-weighted average peer level.
        """
        levels = model.project_levels()
        if not levels:
            return None
        counts = {p: sum(s.project == p for s in sales) for p in levels}
        total = sum(counts.values())
        average = sum(levels[p] * counts[p] for p in levels) / total
        return _pct(-average)

    def income_budget(
        self,
        monthly_income: float,
        down_payment_pct: float,
        loan_tenure_years: int,
        stress_rate_pct: float,
    ) -> tuple[float, MortgageAssumptions]:
        """Work out a budget from income, returning it with the assumptions used."""
        max_price = max_affordable_price(
            monthly_income, down_payment_pct, loan_tenure_years, stress_rate_pct
        )
        assumptions: MortgageAssumptions = {
            "monthly_income": monthly_income,
            "down_payment_pct": down_payment_pct,
            "loan_tenure_years": loan_tenure_years,
            "stress_rate_pct": stress_rate_pct,
            "tdsr_pct": TDSR_PCT,
        }
        return max_price, assumptions

    def rank_nearby_growth(self, query: GrowthQuery) -> NearbyGrowth:
        """Rank the nearest projects by their resale psf trend, not their current price."""
        repo = self._repository
        profile = self._require_profile(query.project)
        x, y = _num(profile["x"]), _num(profile["y"])
        if x is None or y is None:
            raise ValueError(
                f"{query.project} has no coordinates in the data, so it has no nearest "
                "projects to rank; use compare_nearby, which falls back to the same street "
                "or district."
            )
        window_end = repo.coverage()["last_month"].date()
        rows = repo.nearby_growth(query, x, y)
        target_row = repo.project_growth(query)
        target_growth = _num(target_row["annual_growth"])
        target: GrowthStats = {
            "project": query.project,
            "street": profile["street"],
            "district": profile["district"],
            "tenure": profile["tenure"],
            "lease_year": _num(profile["lease_year"]),
            "transactions": target_row["transactions"] or 0,
            "median_psf": _num(target_row["median_psf"]),
            "median_price": _num(target_row["median_price"]),
            "annual_growth_pct": (None if target_growth is None else round(target_growth * 100, 1)),
        }

        peers: list[GrowthPeer] = []
        for row in rows:
            stats = _growth_row(row)
            peers.append(
                {
                    **stats,
                    "distance_m": int(row["distance_m"]),
                    "better_than_target": (
                        stats["annual_growth_pct"] is not None
                        and target["annual_growth_pct"] is not None
                        and stats["annual_growth_pct"] > target["annual_growth_pct"]
                    ),
                }
            )
        ranked = [
            p
            for p in peers
            if p["transactions"] >= query.min_transactions and p["annual_growth_pct"] is not None
        ]
        ranked.sort(key=lambda p: p["annual_growth_pct"] or 0, reverse=True)
        ranked_names = {p["project"] for p in ranked}
        insufficient = [p for p in peers if p["project"] not in ranked_names]

        size_band: SizeBand | None = None
        if query.min_area_sqft is not None or query.max_area_sqft is not None:
            size_band = {
                "min_area_sqft": query.min_area_sqft,
                "max_area_sqft": query.max_area_sqft,
            }

        notes = [
            (
                "Growth is the annualised psf trend of Resale and Sub Sale units, fitted by "
                "regressing log psf on time over the window. It is a recorded-price trend, "
                "not a forecast."
            )
        ]
        if size_band is None:
            notes.append(
                "No size band: units of different sizes are mixed, so psf growth is noisier."
            )
        if query.tenure != "any":
            notes.append(f"Only {query.tenure} projects are included.")
        if not peers:
            notes.append(
                f"No project with coordinates within {query.radius_m} m matched these filters."
            )
        elif not ranked:
            notes.append(
                f"No peer has {query.min_transactions}+ resales in the band with a measurable "
                "trend; widen the radius, the window or the size band."
            )
        elif len(ranked) < 5:
            notes.append(
                f"Only {len(ranked)} peer projects carry a measurable trend: rankings are thin."
            )
        if target["annual_growth_pct"] is None:
            notes.append(
                "The project itself has fewer than 3 resales in the band, so its own growth "
                "cannot be measured; better_than_target is false for every peer."
            )
        if insufficient:
            notes.append(
                f"{len(insufficient)} of {len(peers)} peers have fewer than "
                f"{query.min_transactions} resales in the band or no measurable trend; they "
                "are listed after the ranked peers without a ranking."
            )
        notes.append("Projects without coordinates cannot be scanned and are left out.")

        return {
            "project": query.project,
            "segment": query.segment,
            "radius_m": query.radius_m,
            "window_start": add_months(window_end, -(query.months - 1)).isoformat(),
            "window_end": window_end.isoformat(),
            "size_band": size_band,
            "tenure_filter": query.tenure,
            "target": target,
            "peers": ranked + insufficient,
            "projects_compared": len(ranked),
            "insufficient_data": len(insufficient),
            "notes": notes,
        }

    def affordability_search(
        self, query: AffordabilityQuery, assumptions: MortgageAssumptions | None = None
    ) -> AffordabilityResult:
        """Projects with recent sales inside the budget, dearest median first."""
        rows = self._repository.affordable_projects(query)
        projects: list[AffordableProject] = [
            {
                "project": row["project"],
                "street": row["street"],
                "district": row["district"],
                "transactions": row["transactions"],
                "median_price": _num(row["median_price"]),
                "min_price": _num(row["min_price"]),
                "median_psf": _num(row["median_psf"]),
                "min_area_sqft": _num(row["min_area_sqft"]),
                "max_area_sqft": _num(row["max_area_sqft"]),
            }
            for row in rows
        ]
        notes = [
            (
                "Each project has at least "
                f"{query.min_transactions} sales at or below the budget; projects are ranked by "
                "median sale price, highest first, so the list starts at the top of the budget."
            )
        ]
        if assumptions is not None:
            notes.append(
                "The budget is an illustrative borrowing capacity at "
                f"{assumptions['stress_rate_pct']:g}% and a {assumptions['tdsr_pct']:g}% "
                "total debt servicing ratio. It ignores existing debts, stamp duty, legal "
                "fees and renovation; it is not a loan offer."
            )
        if query.district is None:
            notes.append("All districts are included; set district to narrow the search.")
        if not projects:
            notes.append(
                "No project has enough sales within the budget; raise max_price, lower "
                "min_transactions, or widen the district or area filter."
            )
        return {
            "max_price": round(query.max_price),
            "assumptions": assumptions,
            "filters": {
                "district": query.district,
                "segment": query.segment,
                "min_area_sqft": query.min_area_sqft,
            },
            "projects": projects,
            "notes": notes,
        }

    def market_activity(self, filters: TransactionFilters, period: str = "month") -> MarketActivity:
        key = validate_period(period)
        repo = self._repository
        rows = {_iso(row["period"]): row for row in repo.market_activity(filters, PERIODS[key])}
        step = PERIOD_MONTHS[key]
        last_period = period_start(repo.coverage()["last_month"].date(), key)

        points: list[ActivityPoint] = []
        if rows:
            current = date.fromisoformat(min(rows))
            while (
                current <= last_period
            ):  # fill gaps so recent and prior windows are calendar based
                row = rows.get(current.isoformat())
                points.append(
                    {
                        "period": current.isoformat(),
                        "transactions": row["transactions"] if row else 0,
                        "new_sales": row["new_sales"] if row else 0,
                        "sub_sales": row["sub_sales"] if row else 0,
                        "resales": row["resales"] if row else 0,
                        "median_price": _num(row["median_price"]) if row else None,
                        "median_psf": _num(row["median_psf"]) if row else None,
                    }
                )
                current = add_months(current, step)

        # The latest period is probably incomplete (URA publishes with a delay), so the recent
        # and prior windows are the complete periods before it.
        window = ACTIVITY_WINDOW[key]
        complete = points[:-1]
        recent = sum(p["transactions"] for p in complete[-window:])
        prior = sum(p["transactions"] for p in complete[-2 * window : -window])
        total = sum(p["transactions"] for p in points)
        notes = []
        if not points:
            notes.append("No transactions match these filters.")
        else:
            notes.append(
                f"The latest {key} ({points[-1]['period']}) may be incomplete, so it is left out of "
                "recent_transactions, prior_transactions and volume_change_pct."
            )
        if filters.segment is None and filters.project is None:
            notes.append("No segment filter: landed and non-landed sales are counted together.")
        return {
            "period": key,
            "segment": filters.segment,
            "points": points,
            "total_transactions": total,
            "average_per_period": round(total / len(points), 1) if points else None,
            "recent_periods": window,
            "recent_transactions": recent,
            "prior_transactions": prior,
            "volume_change_pct": round((recent / prior - 1) * 100, 1) if prior else None,
            "notes": notes,
        }

    def dataset_info(self) -> DatasetInfo:
        repo = self._repository
        coverage = self.coverage()
        facets = repo.dataset_facets()
        counts = {
            row["property_type"] or "unknown": row["transactions"]
            for row in repo.property_type_counts()
        }

        segments: list[SegmentInfo] = []
        for name, types in SEGMENTS.items():
            present = [t for t in types if t in counts]
            if present:
                segments.append(
                    {
                        "segment": name,
                        "property_types": present,
                        "transactions": sum(counts.pop(t) for t in present),
                    }
                )
        # Types URA added that we do not group yet compare only with themselves.
        segments += [
            {"segment": t, "property_types": [t], "transactions": n} for t, n in counts.items()
        ]

        projects_pct = _num(facets["projects_with_coordinates_pct"])
        transactions_pct = _num(facets["transactions_with_coordinates_pct"])
        notes = [
            "A snapshot of URA's last 5 years as of the last load: last_month is the latest sale month.",
            "Prices are SGD and areas are sqft. Dates are month-level.",
        ]
        if transactions_pct is not None and transactions_pct < 100:
            notes.append(
                f"{round(100 - transactions_pct, 1)}% of transactions have no project coordinates "
                "(mostly new launches): compare_nearby uses the same street or district for those."
            )
        if "unknown" in counts or any(s["segment"] == "unknown" for s in segments):
            notes.append("Some rows have no property type: reload the data.")
        return {
            **coverage,
            "districts": list(facets["districts"]),
            "types_of_sale": list(facets["types_of_sale"]),
            "segments": segments,
            "coordinate_coverage": {
                "projects_pct": projects_pct,
                "transactions_pct": transactions_pct,
            },
            "notes": notes,
        }

    def project_summary(self, project: str) -> ProjectSummary:
        repo = self._repository
        name = normalise_project_name(project)
        profile = repo.project_profile(name)
        if profile is None:
            raise ValueError(
                f"Unknown project {name!r}: use search_projects to find the exact name"
            )
        window_end = repo.coverage()["last_month"].date()
        recent_start = add_months(window_end, -(SUMMARY_WINDOW_MONTHS - 1))
        prior_start = add_months(recent_start, -SUMMARY_WINDOW_MONTHS)
        row = repo.project_summary(name, recent_start, prior_start)

        recent_psf, prior_psf = _num(row["recent_median_psf"]), _num(row["prior_median_psf"])
        enough = (
            row["recent_transactions"] >= LOW_SAMPLE and row["prior_transactions"] >= LOW_SAMPLE
        )
        change = (
            round((recent_psf / prior_psf - 1) * 100, 1)
            if enough and recent_psf and prior_psf
            else None
        )
        notes = []
        if row["recent_transactions"] == 0:
            notes.append(
                "No sales in the latest 12 months: the project may be sold out or rarely traded."
            )
        elif row["recent_transactions"] < LOW_SAMPLE:
            notes.append(
                f"Only {row['recent_transactions']} sales in the latest 12 months: "
                "the recent median psf is unreliable."
            )
        if change is None and row["recent_transactions"] and row["prior_transactions"] < LOW_SAMPLE:
            notes.append("Too few sales in the prior 12 months to compare.")
        segment = segment_of(profile["property_type"])[0]
        if segment in ("landed", "strata-landed"):
            notes.append("Landed psf depends on land or strata area: do not compare with condos.")
        return {
            "project": name,
            "street": profile["street"],
            "district": profile["district"],
            "property_type": profile["property_type"],
            "segment": segment,
            "tenure": profile["tenure"],
            "lease_year": _num(profile["lease_year"]),
            "has_coordinates": _num(profile["x"]) is not None,
            "transactions": row["transactions"],
            "first_sale": _iso(row["first_sale"]),
            "last_sale": _iso(row["last_sale"]),
            "sales_mix": {
                "new_sale": row["new_sales"],
                "sub_sale": row["sub_sales"],
                "resale": row["resales"],
            },
            "price": {
                "min": _num(row["min_price"]),
                "median": _num(row["median_price"]),
                "max": _num(row["max_price"]),
            },
            "psf": {
                "p25": _num(row["p25_psf"]),
                "median": _num(row["median_psf"]),
                "p75": _num(row["p75_psf"]),
            },
            "area_sqft": {
                "min": _num(row["min_area_sqft"]),
                "median": _num(row["median_area_sqft"]),
                "max": _num(row["max_area_sqft"]),
            },
            "last_12_months": {
                "recent_start": recent_start.isoformat(),
                "prior_start": prior_start.isoformat(),
                "recent_transactions": row["recent_transactions"],
                "recent_median_psf": recent_psf,
                "prior_transactions": row["prior_transactions"],
                "prior_median_psf": prior_psf,
                "change_pct": change,
            },
            "notes": notes,
        }

    def coverage(self) -> Coverage:
        row = self._repository.coverage()
        return {
            "transactions": row["transactions"],
            "projects": row["projects"],
            "first_month": _iso(row["first_month"]) if row["transactions"] else None,
            "last_month": _iso(row["last_month"]) if row["transactions"] else None,
        }

    @staticmethod
    def _project(row: dict[str, Any]) -> ProjectMatch:
        row = {k: v for k, v in row.items() if k != "edits"}  # internal ranking column
        row["first_sale"] = _iso(row["first_sale"])
        row["last_sale"] = _iso(row["last_sale"])
        return row  # type: ignore[return-value]

    @staticmethod
    def _transaction(row: dict[str, Any]) -> Transaction:
        return {**row, "contract_date": _iso(row["contract_date"])}  # type: ignore[typeddict-item]
