"""Private rental dataset: repository (SQL) and pure yield maths.

`private_rental` is not an MCP dataset of its own. It enriches the private residential tools:
the service in `server.private_residential` composes this repository with its own to put a
rental yield beside a valuation. The layers still only call downwards (service -> repository).
"""

from .repository import PrivateRentalRepository
from .returns import (
    MIN_PEER_PROJECTS,
    MIN_RENTALS,
    MIN_SALES,
    YIELD_RENT_MONTHS,
    YIELD_SALE_MONTHS,
    annual_rent,
    area_bounds,
    divergence_pct,
    gross_yield_pct,
    implied_value,
    plausible_yield,
    total_return_pct,
)

__all__ = [
    "MIN_PEER_PROJECTS",
    "MIN_RENTALS",
    "MIN_SALES",
    "YIELD_RENT_MONTHS",
    "YIELD_SALE_MONTHS",
    "PrivateRentalRepository",
    "annual_rent",
    "area_bounds",
    "divergence_pct",
    "gross_yield_pct",
    "implied_value",
    "plausible_yield",
    "total_return_pct",
]
