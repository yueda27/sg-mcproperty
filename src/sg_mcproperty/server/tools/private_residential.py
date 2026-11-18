"""MCP tools for private residential transactions. A thin adapter over the service.

Signatures and docstrings here are the schema the LLM sees. No SQL and no business rules:
validation, limits and queries live in `sg_mcproperty.server.private_residential`.

Planned tools: rank_projects, market_activity, affordability_search.
"""

from fastmcp import FastMCP

from sg_mcproperty.server.private_residential import (
    DatasetInfo,
    NearbyComparison,
    NearbyQuery,
    PriceTrend,
    PrivateResidentialService,
    ProjectMatch,
    ProjectSummary,
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
        segment: str | None = None,
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
            segment: Optional property group: "non-landed" (condos and apartments), "ec"
                (executive condominiums), "landed" (terrace, semi-detached, detached) or
                "strata-landed". Default: no filter.
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
            segment=segment,
        )
        return service.search_transactions(filters, limit)

    @mcp.tool
    def price_trend(
        project: str | None = None,
        district: int | None = None,
        segment: str = "auto",
        type_of_sale: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        min_area_sqft: float | None = None,
        max_area_sqft: float | None = None,
        period: str = "quarter",
    ) -> PriceTrend:
        """Median price and price per square foot (psf) over time for private residential sales.

        Use it to see whether prices for one project, a district or the whole market have
        risen or fallen. Prices are SGD and areas sqft. Each point is the median of all
        transactions in that period. Filters are optional and combined with AND. Use
        search_projects first to get an exact project name.

        Args:
            project: Exact project name, case-insensitive, e.g. "PARKTOWN RESIDENCE".
            district: Postal district, 1-28.
            segment: "auto" (default: non-landed for market-wide questions, no filter when a
                project is named), "non-landed" (condos and apartments), "ec", "landed",
                "strata-landed" or "all". Never compare psf across landed and non-landed.
            type_of_sale: "New Sale", "Sub Sale" or "Resale". New launch prices differ from
                resale prices, so filter on this when comparing like with like.
            start_date: Earliest month, inclusive, "YYYY-MM" or "YYYY-MM-DD".
            end_date: Latest month, inclusive, same format.
            min_area_sqft: Minimum unit area in sqft, to compare similar sized units.
            max_area_sqft: Maximum unit area in sqft.
            period: "month", "quarter" (default) or "year".

        Returns period (as requested), segment (as applied, null for none), points (oldest
        first, each with period start date, transactions, median_price, median_psf and
        low_sample, true when there are fewer than 5 transactions), psf_change_pct (change in
        median psf from the first to the last period that is not low_sample, null if fewer
        than two) and notes. Treat low_sample periods as noise.
        """
        filters = TransactionFilters.from_inputs(
            project,
            district,
            type_of_sale,
            start_date,
            end_date,
            min_area_sqft=min_area_sqft,
            max_area_sqft=max_area_sqft,
            segment=segment,
        )
        return service.price_trend(filters, period)

    @mcp.tool
    def compare_nearby(
        project: str,
        radius_m: int = 1000,
        months: int = 12,
        type_of_sale: str | None = None,
        area_sqft: float | None = None,
        same_tenure_type: bool = False,
        max_peers: int = 10,
        lease_year_tolerance: int | None = None,
    ) -> NearbyComparison:
        """Compare one project's price per sqft (psf) with similar projects nearby, to value it.

        Finds projects of the same property type within `radius_m` metres, then compares
        median psf over the latest `months` months of data. Use it for "is this project
        expensive for its area?" and, with `area_sqft`, for an indicative price. Use
        search_projects first to get the exact project name. Prices are SGD, areas sqft.

        The project and its peers are filtered identically (same sale type, same size band,
        same period), so the comparison is like for like. The result is an indicative
        comparison of recorded prices, not a formal valuation: it ignores floor, view, unit
        condition and project age. Always show the user the peers and the notes.

        Args:
            project: Exact project name, case-insensitive.
            radius_m: Search radius in metres, 100-3000 (default 1000).
            months: Window length in months ending at the latest data month, 1-60 (default 12).
            type_of_sale: Restrict to "New Sale", "Sub Sale" or "Resale" for the project and
                its peers. Recommended: new launches and resales are priced differently.
            area_sqft: Unit size of interest. Restricts everything to units within +/-20% of
                it and adds indicative_value (peer psf x this area).
            same_tenure_type: If true, peers must match the project's freehold or leasehold
                status.
            max_peers: Maximum number of peer projects, 1-20 (default 10, nearest first).
            lease_year_tolerance: Only compare with leasehold projects whose lease started
                within this many years of the project's (0-60), e.g. 10. Age is a big driver
                of psf, so use it when peers are much older or newer. Ignored for freehold
                projects. Default: no age filter.

        Returns: basis ("radius", or "street" then "district" when the project has no coordinates,
        which is common for new launches: peers are then the busiest projects on the same street
        or in the same district, and distance_m is null),
        target (street, district, property_type, tenure, lease_year and stats with
        transactions, median_psf, p25_psf, p75_psf, median_price), peers (each with
        distance_m, transactions, median_psf, median_price, tenure and lease_year),
        peer_group (pooled stats across the returned peers, plus projects count),
        premium_pct (project median psf versus peer median psf, positive means the project is
        pricier), indicative_value (low/mid/high SGD from the peer 25th, 50th and 75th
        percentile psf, only when area_sqft is given), window_start, window_end, and notes
        with warnings such as small samples or tenure mismatches. Any value can be null when
        there is no data.
        """
        query = NearbyQuery.from_inputs(
            project,
            radius_m,
            months,
            type_of_sale,
            area_sqft,
            same_tenure_type,
            max_peers,
            lease_year_tolerance,
        )
        return service.compare_nearby(query)

    @mcp.tool
    def dataset_info() -> DatasetInfo:
        """Describe the private residential dataset: what it covers and which filter values are valid.

        Call this first when unsure what data exists, how fresh it is, or which values a filter
        accepts (districts, sale types, segments). It takes no arguments. It is the data's own
        description, so prefer it to guessing or to remembering earlier figures.

        Returns transactions (total sales on record), projects (distinct projects), first_month
        and last_month (YYYY-MM-DD, first of month: the earliest and latest sale month, so
        last_month shows how fresh the data is), districts (postal districts present, 1-28),
        types_of_sale ("New Sale", "Sub Sale", "Resale"), segments (each with its segment name,
        the property types it groups and its transaction count; the segment names are the valid
        `segment` values for other tools), coordinate_coverage (percent of projects and of
        transactions that have a location; compare_nearby needs one) and notes.
        """
        return service.dataset_info()

    @mcp.tool
    def project_summary(project: str) -> ProjectSummary:
        """Profile one private residential project: what it is, its prices and how they are moving.

        Use search_projects first to get the exact project name. Prices are SGD and areas
        sqft. Figures cover all of the project's sales in the last 5 years unless stated.
        For individual sales use search_transactions, for a time series use price_trend, and
        to judge whether it is priced high or low against its neighbours use compare_nearby.

        Args:
            project: Exact project name, case-insensitive, e.g. "PARKTOWN RESIDENCE".

        Returns: project, street, district, property_type, segment (the group its psf is
        comparable within), tenure (e.g. "99 years", "Freehold"), lease_year (lease start, null
        for freehold), has_coordinates, transactions, first_sale and last_sale (YYYY-MM-DD,
        first of month), sales_mix (counts of new_sale, sub_sale and resale), price (min,
        median, max in SGD), psf (p25, median and p75 price per sqft in SGD), area_sqft (min,
        median, max), last_12_months (median psf and transaction counts for the latest 12 months
        versus the 12 months before, and change_pct, null unless both periods have at least 5
        sales) and notes with warnings such as thin samples or no recent sales. Any value can
        be null when there is no data.
        """
        return service.project_summary(project)
