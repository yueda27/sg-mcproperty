"""Use cases for private residential data: validate, apply limits, shape results.

Calls the repository for rows and returns typed, JSON friendly results. Knows nothing about
SQL or MCP.
"""

from datetime import datetime
from typing import Any, TypedDict

from .filters import TransactionFilters, normalise_project_query, validate_district
from .repository import PrivateResidentialRepository

MAX_PROJECT_RESULTS = 25
MAX_TRANSACTION_RESULTS = 100


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


class Coverage(TypedDict):
    transactions: int
    projects: int
    first_month: str | None
    last_month: str | None


def _iso(value: Any) -> Any:
    return value.date().isoformat() if isinstance(value, datetime) else value


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
