"""MCP tools for private residential transactions. A thin adapter over the service.

Signatures and docstrings here are the schema the LLM sees. No SQL and no business rules:
validation, limits and queries live in `sg_mcproperty.server.private_residential`.

Planned tools: dataset_info, price_trend, project_summary, compare, rank_projects,
market_activity, find_comparables, affordability_search.
"""

from fastmcp import FastMCP

from sg_mcproperty.server.private_residential import (
    PrivateResidentialService,
    ProjectMatch,
    TransactionFilters,
    TransactionPage,
)


def register(mcp: FastMCP, service: PrivateResidentialService) -> None:
    """Register private residential tools on `mcp`."""

    @mcp.tool
    def search_projects(
        name_query: str, district: int | None = None, limit: int = 10
    ) -> list[ProjectMatch]:
        """Find private residential projects (condos, apartments, landed estates) by name.

        Use this first to get the exact `project` value that other tools need. Matching is
        case-insensitive and every word in `name_query` must appear in the project name, so
        "parktown res" finds "PARKTOWN RESIDENCE". If nothing matches, a typo tolerant search
        is tried and those rows have match_type "fuzzy": confirm them with the user. An empty
        result means no similar project, so try a shorter name.

        Args:
            name_query: Part of a project name, at least 2 characters, e.g. "parktown".
            district: Optional postal district to restrict to, 1-28.
            limit: Max projects to return (default 10, capped at 25).

        Returns a list of projects, best match first, each with: project (exact name),
        street, district (1-28), tenure (e.g. "99 years", "Freehold"), lease_year (lease
        commencement year, null for freehold), transactions (number of sales on record,
        last 5 years), first_sale and last_sale (YYYY-MM-DD, first day of the month), and
        match_type ("exact", "prefix", "contains" or "fuzzy").
        """
        return service.search_projects(name_query, district, limit)

    @mcp.tool
    def search_transactions(
        project: str | None = None,
        district: int | None = None,
        type_of_sale: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        min_price: float | None = None,
        max_price: float | None = None,
        min_area_sqft: float | None = None,
        max_area_sqft: float | None = None,
        limit: int = 20,
    ) -> TransactionPage:
        """List individual private residential transactions (last 5 years), newest first.

        All filters are optional and combined with AND. Use search_projects first to get the
        exact `project` name. Prices are in SGD and areas in square feet (sqft). Dates are
        month-level: the sale month for New Sale, the option exercised month for Resale and
        Sub Sale. Landed property has no floor (null) and its psf is not comparable to condos.

        Args:
            project: Exact project name, case-insensitive, e.g. "PARKTOWN RESIDENCE".
            district: Postal district, 1-28.
            type_of_sale: "New Sale", "Sub Sale" or "Resale" (case-insensitive).
            start_date: Earliest month, inclusive, "YYYY-MM" or "YYYY-MM-DD", e.g. "2025-01".
            end_date: Latest month, inclusive, same format.
            min_price: Minimum price in SGD.
            max_price: Maximum price in SGD.
            min_area_sqft: Minimum area in sqft.
            max_area_sqft: Maximum area in sqft.
            limit: Max rows to return (default 20, capped at 100).

        Returns total_matches (all rows matching the filters), returned (rows in this
        response) and transactions, each with: project, street, district, contract_date
        (YYYY-MM-DD, first of month), type_of_sale, price (SGD), area_sqft, psf (SGD per
        sqft), floor (midpoint of the floor range, null for landed), tenure and lease_year
        (null for freehold). If total_matches exceeds returned, narrow the filters.
        """
        filters = TransactionFilters.from_inputs(
            project,
            district,
            type_of_sale,
            start_date,
            end_date,
            min_price,
            max_price,
            min_area_sqft,
            max_area_sqft,
        )
        return service.search_transactions(filters, limit)
