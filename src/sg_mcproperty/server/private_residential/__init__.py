"""Private residential dataset: filters -> repository (SQL) -> service (use cases).

Each layer only calls the one below it. The MCP adapters in `server/tools`, `server/prompts`
and `server/resources` depend on the service only.
"""

from .filters import TransactionFilters
from .repository import PrivateResidentialRepository
from .service import (
    Coverage,
    PrivateResidentialService,
    ProjectMatch,
    Transaction,
    TransactionPage,
)

__all__ = [
    "Coverage",
    "PrivateResidentialRepository",
    "PrivateResidentialService",
    "ProjectMatch",
    "Transaction",
    "TransactionFilters",
    "TransactionPage",
]
