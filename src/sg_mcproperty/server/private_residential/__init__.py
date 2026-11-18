"""Private residential dataset: filters -> repository (SQL) -> service (use cases).

Each layer only calls the one below it. The MCP adapters in `server/tools`, `server/prompts`
and `server/resources` depend on the service only.
"""

from .filters import NearbyQuery, TransactionFilters, ValuationQuery
from .repository import PrivateResidentialRepository
from .service import (
    Coverage,
    DatasetInfo,
    MarketActivity,
    NearbyComparison,
    PriceTrend,
    PrivateResidentialService,
    ProjectMatch,
    ProjectSummary,
    Transaction,
    TransactionPage,
    ValueEstimate,
)

__all__ = [
    "Coverage",
    "DatasetInfo",
    "MarketActivity",
    "NearbyComparison",
    "NearbyQuery",
    "PriceTrend",
    "PrivateResidentialRepository",
    "PrivateResidentialService",
    "ProjectMatch",
    "ProjectSummary",
    "Transaction",
    "TransactionFilters",
    "TransactionPage",
    "ValuationQuery",
    "ValueEstimate",
]
