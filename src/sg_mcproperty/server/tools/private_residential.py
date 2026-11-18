"""MCP tools for private residential transactions. A thin adapter over the service.

Signatures and docstrings here are the schema the LLM sees. No SQL and no business rules:
validation, limits and queries live in `sg_mcproperty.server.private_residential`.

Tools that need information the caller did not supply use `server.elicitation.ask`, which
works on both MCP protocol eras. Those tools are typed `Result | InputRequiredResult`.

Planned tool: rank_projects.
"""

from dataclasses import dataclass, field, make_dataclass
from typing import Literal

from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError
from mcp.types import InputRequiredResult

from sg_mcproperty.server.elicitation import ask
from sg_mcproperty.server.private_residential import (
    AffordabilityQuery,
    AffordabilityResult,
    DatasetInfo,
    GrowthQuery,
    MarketActivity,
    NearbyComparison,
    NearbyGrowth,
    NearbyQuery,
    PriceTrend,
    PrivateResidentialService,
    ProjectMatch,
    ProjectSummary,
    TransactionFilters,
    TransactionPage,
    ValuationQuery,
    ValueEstimate,
)


@dataclass
class UnitDetails:
    """What estimate_value asks for when it has no unit size."""

    area_sqft: float = field(
        metadata={"title": "Unit size (sqft)", "description": "Floor area in square feet"}
    )
    floor: int = field(
        default=0,
        metadata={
            "title": "Floor",
            "description": "Floor number; 0 means typical floor for the project",
        },
    )
    type_of_sale: Literal["Resale", "New Sale"] = field(
        default="Resale",
        metadata={"description": "Resale, or New Sale for a unit bought from the developer"},
    )


def _comparison_basis_form(
    type_of_sale: str | None, same_tenure_type: bool, lease_year_tolerance: int | None
):
    """Comparison basis, pre-filled with whatever the caller already passed."""
    sales = ("Resale", "New Sale", "Sub Sale")
    sale = next(
        (name for name in sales if name.casefold() == (type_of_sale or "").casefold()), "Any"
    )
    return make_dataclass(
        "ComparisonBasis",
        [
            (
                "type_of_sale",
                Literal["Resale", "New Sale", "Sub Sale", "Any"],
                field(
                    default=sale,
                    metadata={"description": "New launch and resale prices differ"},
                ),
            ),
            (
                "same_tenure_type",
                bool,
                field(
                    default=same_tenure_type,
                    metadata={"description": "Only compare freehold with freehold, etc."},
                ),
            ),
            (
                "lease_year_tolerance",
                int,
                field(
                    default=lease_year_tolerance or 0,
                    metadata={
                        "description": "Match lease start within this many years; 0 = no filter"
                    },
                ),
            ),
        ],
    )


def _growth_scope_form(
    radius_m: int,
    max_peers: int,
    months: int,
    min_area_sqft: float | None,
    max_area_sqft: float | None,
    min_transactions: int,
    tenure: str,
):
    """Scan scope, pre-filled with whatever the caller already passed."""
    tenures = ("any", "freehold", "999 years", "freehold or 999 years")
    return make_dataclass(
        "GrowthScanScope",
        [
            ("radius_m", int, field(default=radius_m, metadata={"title": "Radius (m)"})),
            ("max_peers", int, field(default=max_peers, metadata={"title": "Nearest projects"})),
            ("months", int, field(default=months, metadata={"title": "Window (months)"})),
            (
                "tenure",
                Literal["any", "freehold", "999 years", "freehold or 999 years"],
                field(
                    default=tenure if tenure in tenures else "any",
                    metadata={"description": "Tenure filter"},
                ),
            ),
            (
                "min_area_sqft",
                float,
                field(default=min_area_sqft or 0, metadata={"description": "0 = no minimum"}),
            ),
            (
                "max_area_sqft",
                float,
                field(default=max_area_sqft or 0, metadata={"description": "0 = no maximum"}),
            ),
            (
                "min_transactions",
                int,
                field(
                    default=min_transactions,
                    metadata={"description": "Resales a project needs before it is ranked"},
                ),
            ),
        ],
    )


def _affordability_form(
    max_price: float | None, district: int | None, segment: str, min_area_sqft: float | None
):
    """Budget and loan assumptions, pre-filled with whatever the caller already passed."""
    segments = ("non-landed", "ec", "landed", "strata-landed")
    return make_dataclass(
        "AffordabilityProfile",
        [
            (
                "max_price",
                float,
                field(
                    default=max_price or 0,
                    metadata={
                        "title": "Budget (SGD)",
                        "description": "Total purchase price; 0 = work it out from income",
                    },
                ),
            ),
            (
                "monthly_income",
                float,
                field(
                    default=0,
                    metadata={
                        "title": "Gross monthly income (SGD)",
                        "description": "Used only when budget is 0",
                    },
                ),
            ),
            (
                "down_payment_pct",
                float,
                field(default=25, metadata={"description": "Cash plus CPF down payment, %"}),
            ),
            (
                "loan_tenure_years",
                int,
                field(default=25, metadata={"description": "Mortgage term in years"}),
            ),
            (
                "stress_rate_pct",
                float,
                field(default=4.0, metadata={"description": "Interest rate used for the test"}),
            ),
            (
                "district",
                int,
                field(default=district or 0, metadata={"description": "Postal district; 0 = all"}),
            ),
            (
                "segment",
                Literal["non-landed", "ec", "landed", "strata-landed"],
                field(
                    default=segment if segment in segments else "non-landed",
                    metadata={"description": "Property group"},
                ),
            ),
            (
                "min_area_sqft",
                float,
                field(default=min_area_sqft or 0, metadata={"description": "0 = any size"}),
            ),
        ],
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
    async def compare_nearby(
        project: str,
        ctx: Context,
        radius_m: int = 1000,
        months: int = 12,
        type_of_sale: str | None = None,
        area_sqft: float | None = None,
        same_tenure_type: bool = False,
        max_peers: int = 10,
        lease_year_tolerance: int | None = None,
        ask_basis: bool = False,
    ) -> NearbyComparison | InputRequiredResult:
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
            max_peers: Maximum number of peer projects, 1-50 (default 10, nearest first).
            lease_year_tolerance: Only compare with leasehold projects whose lease started
                within this many years of the project's (0-60), e.g. 10. Age is a big driver
                of psf, so use it when peers are much older or newer. Ignored for freehold
                projects. Default: no age filter.
            ask_basis: Set true when the user has not said which sale type to compare, or
                whether to match tenure type and age. The tool asks them first; declining
                keeps the arguments above.

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
        if ask_basis:
            form = _comparison_basis_form(type_of_sale, same_tenure_type, lease_year_tolerance)
            answer = await ask(
                ctx,
                "comparison_basis",
                "How should the comparison be set up? New launch and resale prices differ, and "
                "tenure and project age move psf a lot.",
                form,
            )
            if answer.pending is not None:
                return answer.pending
            if answer.value is not None:
                type_of_sale = (
                    None if answer.value.type_of_sale == "Any" else answer.value.type_of_sale
                )
                same_tenure_type = answer.value.same_tenure_type
                lease_year_tolerance = answer.value.lease_year_tolerance or None

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
    async def rank_nearby_growth(
        project: str,
        ctx: Context,
        radius_m: int = 2000,
        months: int = 60,
        min_area_sqft: float | None = None,
        max_area_sqft: float | None = None,
        max_peers: int = 50,
        min_transactions: int = 4,
        segment: str = "non-landed",
        tenure: str = "any",
        ask_scope: bool = False,
    ) -> NearbyGrowth | InputRequiredResult:
        """Rank the nearest projects by psf growth, to find neighbours that grew faster.

        Scans the nearest `max_peers` projects (by SVY21 distance, so a project without
        coordinates cannot be scanned) and fits each one's annualised psf trend over the
        window, using only Resale and Sub Sale units. Use it for "which projects near X have
        grown faster?" and then compare_nearby or estimate_value on the winners. Growth is a
        recorded-price trend, not a forecast.

        Args:
            project: Exact project name, case-insensitive.
            radius_m: Search radius in metres, 100-3000 (default 2000).
            months: Window in months ending at the latest data month, 1-60 (default 60).
            min_area_sqft: Minimum unit area in sqft for like-for-like psf (optional).
            max_area_sqft: Maximum unit area in sqft (optional).
            max_peers: Number of nearest projects to scan, 1-50 (default 50).
            min_transactions: Resales a project needs in the band before it is ranked
                (1-50, default 4). Projects below it are returned after the ranked ones and
                are not ranked.
            segment: Property group: "non-landed" (default), "ec", "landed" or
                "strata-landed". Do not mix landed and non-landed psf.
            tenure: "any" (default), "freehold", "999 years" or "freehold or 999 years".
            ask_scope: Set true when the user has not fixed the radius, peer count, window,
                tenure or size band. The tool asks first; declining keeps the arguments above.

        Returns: target (the project's own stats and annual_growth_pct), peers (nearest
        projects; ranked ones first, highest growth first, then the rest by distance), each
        with distance_m, transactions, median_psf, median_price, annual_growth_pct and
        better_than_target; window_start, window_end, radius_m, size_band, tenure_filter;
        projects_compared (peers with a usable trend), insufficient_data (peers returned
        without one) and notes. annual_growth_pct is the annualised psf trend, fitted by
        regressing log psf on time: 4.5 means +4.5% a year. It is null when a project has
        fewer than 3 resales in the band.
        """
        if ask_scope:
            form = _growth_scope_form(
                radius_m,
                max_peers,
                months,
                min_area_sqft,
                max_area_sqft,
                min_transactions,
                tenure,
            )
            answer = await ask(
                ctx,
                "growth_scope",
                "Confirm the growth scan before it runs: distance, number of projects, window, "
                "tenure and unit-size band.",
                form,
            )
            if answer.pending is not None:
                return answer.pending
            if answer.value is not None:
                scope = answer.value
                radius_m = scope.radius_m
                max_peers = scope.max_peers
                months = scope.months
                tenure = scope.tenure
                min_area_sqft = scope.min_area_sqft or None
                max_area_sqft = scope.max_area_sqft or None
                min_transactions = scope.min_transactions

        query = GrowthQuery.from_inputs(
            project,
            radius_m,
            months,
            max_peers,
            min_transactions,
            min_area_sqft,
            max_area_sqft,
            segment,
            tenure,
        )
        return service.rank_nearby_growth(query)

    @mcp.tool
    async def affordability_search(
        ctx: Context,
        max_price: float | None = None,
        district: int | None = None,
        segment: str = "non-landed",
        min_area_sqft: float | None = None,
        min_transactions: int = 3,
        limit: int = 20,
        ask_profile: bool = False,
    ) -> AffordabilityResult | InputRequiredResult:
        """Find projects with recent sales inside a budget, dearest median price first.

        Use it for "what can I afford?" questions. Pass `max_price` when the user gave a
        budget, or leave it out and the tool asks for a budget or a monthly income plus loan
        assumptions. The borrowing capacity is illustrative: it counts 55% of gross monthly
        income at the stress rate and grosses up for the down payment, ignoring existing
        debts, stamp duty, legal fees and renovation. It is not a loan offer.

        Args:
            max_price: Total purchase price in SGD. Omit to be asked, or to compute it from
                monthly income.
            district: Postal district, 1-28. Omit for all districts.
            segment: "non-landed" (default), "ec", "landed" or "strata-landed".
            min_area_sqft: Minimum unit area in sqft (optional).
            min_transactions: Sales a project needs inside the budget before it is listed,
                1-50 (default 3).
            limit: Max projects to return, 1-50 (default 20).
            ask_profile: Set true to confirm the budget and loan assumptions even when a
                budget was passed. The tool asks first; declining keeps the arguments.

        Returns: max_price (the budget used), assumptions (the income, down payment, loan
        tenure, stress rate and 55% TDSR used; null when a budget was passed directly),
        filters (district, segment, min_area_sqft), projects (each with street, district,
        transactions inside the budget, median and minimum price, median psf and area range,
        highest median price first) and notes. Sales are the last 5 years; prices are SGD and
        areas sqft.
        """
        assumptions = None
        if ask_profile or max_price is None:
            form = _affordability_form(max_price, district, segment, min_area_sqft)
            answer = await ask(
                ctx,
                "affordability",
                "What is the budget, or the gross monthly income and loan assumptions to work "
                "one out? All figures are in SGD.",
                form,
            )
            if answer.pending is not None:
                return answer.pending
            if answer.value is None:
                raise ToolError(
                    "A budget or a monthly income is needed to search; pass max_price or "
                    "retry the form."
                )
            profile = answer.value
            district = profile.district or None
            segment = profile.segment
            min_area_sqft = profile.min_area_sqft or None
            max_price = profile.max_price or None
            if max_price is None:
                if profile.monthly_income <= 0:
                    raise ToolError(
                        "The form needs either a budget or a positive gross monthly income."
                    )
                max_price, assumptions = service.income_budget(
                    profile.monthly_income,
                    profile.down_payment_pct,
                    profile.loan_tenure_years,
                    profile.stress_rate_pct,
                )

        query = AffordabilityQuery.from_inputs(
            max_price,
            district,
            segment,
            min_area_sqft,
            min_transactions,
            limit,
        )
        return service.affordability_search(query, assumptions)

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

    @mcp.tool
    def market_activity(
        project: str | None = None,
        district: int | None = None,
        segment: str = "auto",
        type_of_sale: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        period: str = "month",
    ) -> MarketActivity:
        """How many private residential sales happen over time, split by sale type.

        Use it to judge liquidity and momentum before trusting a price: a project or area
        with few sales gives a shaky median, and a falling or rising sales volume says how the
        market is moving. Call it before or alongside estimate_value, price_trend and
        compare_nearby. Filters are optional and combined with AND. Use search_projects first
        to get an exact project name.

        Args:
            project: Exact project name, case-insensitive, e.g. "PARKTOWN RESIDENCE".
            district: Postal district, 1-28.
            segment: "auto" (default: non-landed for market-wide questions, no filter when a
                project is named), "non-landed", "ec", "landed", "strata-landed" or "all".
            type_of_sale: Optionally count only "New Sale", "Sub Sale" or "Resale".
            start_date: Earliest month, inclusive, "YYYY-MM" or "YYYY-MM-DD".
            end_date: Latest month, inclusive, same format.
            period: "month" (default), "quarter" or "year".

        Returns points (oldest first, every period filled in so quiet periods show 0, each
        with period start date, transactions, new_sales, sub_sales, resales, median_price and
        median_psf in SGD), total_transactions, average_per_period, recent_periods (how many
        periods "recent" means: 3 months, 2 quarters or 1 year), recent_transactions,
        prior_transactions (the same number of periods before that), volume_change_pct (recent
        versus prior, null if there were no prior sales) and notes. The latest period is
        probably incomplete because URA publishes with a delay, so it is shown in points but
        left out of recent_transactions, prior_transactions and volume_change_pct.
        """
        filters = TransactionFilters.from_inputs(
            project,
            district,
            type_of_sale,
            start_date,
            end_date,
            segment=segment,
        )
        return service.market_activity(filters, period)

    @mcp.tool
    async def estimate_value(
        project: str,
        ctx: Context,
        area_sqft: float | None = None,
        floor: int | None = None,
        type_of_sale: str | None = None,
        radius_m: int = 1500,
        months: int = 24,
        max_peers: int = 12,
    ) -> ValueEstimate | InputRequiredResult:
        """Estimate what a unit in a project would sell for, with a range, using regression.

        Fits a regression of log price on recent sales of the project and similar projects
        nearby (same property type, within `radius_m` metres), controlling for unit size,
        floor, new-sale versus resale and the month (market trend), plus remaining lease and
        freehold when the project has too few sales of its own. It then predicts the price of the
        unit described by the arguments at the latest month of data.

        If `area_sqft` is omitted, the tool first asks for the unit size, floor and sale type
        (declining is an error, because there is nothing to value). Two modes (model.mode).
        "project": the project has at least 5 sales of its own, so each project gets its own
        price level and size, floor, sale-type and trend effects are learned from variation
        within projects; the estimate sits at this project's own price level. "area": the
        project has too few sales, so the model compares across projects and the estimate
        reflects the surrounding area. Its lease and freehold effects are less reliable because
        they can be confounded with project quality. Use search_projects first to get the exact
        project name. Prices are SGD and areas sqft.

        This is a statistical estimate from recorded prices, not a formal valuation: it cannot
        see view, facing, renovation or the exact unit. Always show the user the range, the
        notes, and the comparable_check. For context call market_activity (is the market
        liquid and which way is it moving) and compare_nearby (how the project compares).

        Args:
            project: Exact project name, case-insensitive.
            area_sqft: Size of the unit in square feet. Sales from half to double this size
                are used. Omit and the tool asks for it.
            floor: Floor of the unit, 1-100. Default: the floor typically sold in the project.
                Ignored for landed property, which has no floor.
            type_of_sale: "New Sale" or "Resale" ("Sub Sale" is treated like resale). Default
                is resale. New launches and resales price differently, so set this for a
                unit bought from a developer.
            radius_m: Radius in metres for similar projects, 100-3000 (default 1500). Projects
                without coordinates use the same street, then district, instead.
            months: Months of sales to use, ending at the latest data month, 6-60 (default 24).
            max_peers: Max similar projects to include, 1-50 (default 12, nearest first).

        Returns: estimate (median, low and high in SGD, an 80% range, and the implied psf; null
        if there are too few comparable sales), unit (what was valued, including remaining
        lease), model (mode, sales_used, projects, r_squared, typical_error_pct,
        outliers_removed and the features fitted), effects (what the model learned, as
        percentages: price change for 10% more area, per 10 floors, per 10 years of lease, the
        freehold and new-sale premiums, the monthly trend, and target_project_premium_pct, this
        project's price level versus similar projects; null where not estimable), comparable_check
        (the median psf of the project's own sales within 20% of the size, or of nearby sales if
        the project has too few, and the value it implies, as a sanity check on the regression;
        source says which), activity (sales in the sample over the last 3 complete months versus
        the 3 before, as a liquidity and momentum signal), peers (the projects
        used) and notes (warnings: small sample, weak fit, extrapolation, no project-specific
        effect). Large gaps between estimate and comparable_check mean the estimate is shaky.
        """
        if area_sqft is None:
            answer = await ask(
                ctx,
                "unit_details",
                "What unit should be valued? Give its size, floor and sale type.",
                UnitDetails,
            )
            if answer.pending is not None:
                return answer.pending
            if answer.value is None:
                raise ToolError(
                    "A unit size is required to estimate a value; pass area_sqft or answer "
                    "the form."
                )
            area_sqft = answer.value.area_sqft
            floor = floor if floor is not None else (answer.value.floor or None)
            if type_of_sale is None:
                type_of_sale = answer.value.type_of_sale

        query = ValuationQuery.from_inputs(
            project, area_sqft, floor, type_of_sale, radius_m, months, max_peers
        )
        return service.estimate_value(query)
