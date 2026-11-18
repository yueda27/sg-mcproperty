"""Private residential dataset: filters -> repository (SQL) -> service (use cases).

Each layer only calls the one below it. The MCP adapters in `server/tools`, `server/prompts`
and `server/resources` depend on the service only.
"""

from .filters import (
    AffordabilityQuery,
    GrowthQuery,
    NearbyQuery,
    TransactionFilters,
    ValuationQuery,
)
from .repository import PrivateResidentialRepository
from .service import (
    AffordabilityResult,
    Coverage,
    DatasetInfo,
    MarketActivity,
    NearbyComparison,
    NearbyGrowth,
    PriceTrend,
    PrivateResidentialService,
    ProjectMatch,
    ProjectSummary,
    ProjectYield,
    Transaction,
    TransactionPage,
    ValueEstimate,
    max_affordable_price,
)

__all__ = [
    "AffordabilityQuery",
    "AffordabilityResult",
    "Coverage",
    "DatasetInfo",
    "GrowthQuery",
    "MarketActivity",
    "NearbyComparison",
    "NearbyGrowth",
    "NearbyQuery",
    "PriceTrend",
    "PrivateResidentialRepository",
    "PrivateResidentialService",
    "ProjectMatch",
    "ProjectSummary",
    "ProjectYield",
    "Transaction",
    "TransactionFilters",
    "TransactionPage",
    "ValuationQuery",
    "ValueEstimate",
    "max_affordable_price",
]
