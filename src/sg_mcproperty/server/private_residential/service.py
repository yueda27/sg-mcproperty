"""Use cases for private residential data: validate, apply limits, shape results.

Calls the repository for rows and returns typed, JSON friendly results. Knows nothing about
SQL or MCP.
"""

import math
from dataclasses import replace
from datetime import date, datetime
from typing import Any, TypedDict

from .filters import (
    AGE_GAP_NOTE_YEARS,
    PERIODS,
    SEGMENTS,
    NearbyQuery,
    PeerFilters,
    TransactionFilters,
    add_months,
    normalise_project_name,
    normalise_project_query,
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


class Coverage(TypedDict):
    transactions: int
    projects: int
    first_month: str | None
    last_month: str | None


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
        profile = repo.project_profile(query.project)
        if profile is None:
            raise ValueError(
                f"Unknown project {query.project!r}: use search_projects to find the exact name"
            )
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

        x, y = _num(profile["x"]), _num(profile["y"])
        if x is not None and y is not None:
            basis = "radius"
            peer_rows = repo.find_peers_by_distance(
                query.project, x, y, query.radius_m, peer_filters, MIN_PEER_TRANSACTIONS,
                query.max_peers,
            )  # fmt: skip
        else:
            # No coordinates (common for new launches): same street is the closest proxy,
            # then the same district.
            basis = "street"
            peer_rows = repo.find_peers_by_area(
                query.project, "street", profile["street"], peer_filters, MIN_PEER_TRANSACTIONS,
                query.max_peers,
            )  # fmt: skip
            if not peer_rows:
                basis = "district"
                peer_rows = repo.find_peers_by_area(
                    query.project, "district", profile["district"], peer_filters,
                    MIN_PEER_TRANSACTIONS, query.max_peers,
                )  # fmt: skip

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

        peers: list[PeerProject] = [
            {**{k: _num(v) for k, v in row.items()}, "distance_m": _num(row["distance_m"])}
            for row in peer_rows
        ]  # type: ignore[misc]
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
