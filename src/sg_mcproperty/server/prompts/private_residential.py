"""MCP prompts for private residential data: reusable starting points a user can pick.

A prompt returns instructions for the model, telling it which tools to use and how to present
the answer. It does not fetch data itself. Only reference tools that exist.
"""

from fastmcp import FastMCP


def register(mcp: FastMCP) -> None:
    """Register private residential prompts on `mcp`."""

    @mcp.prompt
    def project_overview(project_name: str) -> str:
        """Summarise recent sales at one private residential project."""
        return (
            f"Give me an overview of recent sales at the private residential project "
            f'"{project_name}".\n\n'
            "1. Call search_projects with this name to get the exact project name. If there are "
            "several matches, or the only match has match_type 'fuzzy', ask me which one I mean "
            "before continuing.\n"
            "2. Call project_summary for that exact project for its profile: sales mix, price and "
            "psf ranges, tenure and lease start, and the latest 12 months versus the 12 before.\n"
            "3. If I want examples, call search_transactions for the project, newest first.\n"
            "Summarise: how many sales are on record, the price and psf range, the mix of New Sale "
            "versus Resale, the tenure and lease start year, and whether psf is rising or falling "
            "(only if change_pct is not null).\n"
            "Prices are SGD and areas are sqft. Quote figures only from the tool results, and "
            "say when the sample is small."
        )

    @mcp.prompt
    def recent_deals(district: int, months: int = 6) -> str:
        """List the biggest recent transactions in a postal district."""
        return (
            f"Show me notable private residential transactions in postal district {district} "
            f"over the last {months} months.\n\n"
            "1. Work out the start month (YYYY-MM) that is "
            f"{months} months before the latest month in the data. Call the "
            "dataset_info tool if you need the latest month in the data.\n"
            f"2. Call search_transactions with district={district} and that start_date.\n"
            "3. Report total_matches, then the highest-priced sales with project, price, area "
            "and psf. If total_matches is larger than what was returned, say the list is "
            "partial.\n"
            "Prices are SGD and areas are sqft. Do not compare landed property (no floor) with "
            "condos on psf."
        )

    @mcp.prompt
    def value_project(project_name: str, area_sqft: float | None = None) -> str:
        """Judge whether a project is priced high or low and estimate what a unit is worth."""
        size = f" for a unit of about {area_sqft:g} sqft" if area_sqft else ""
        area_arg = f"area_sqft={area_sqft:g}" if area_sqft else "area_sqft (ask me for the size)"
        return (
            f'Assess "{project_name}"{size}: is it priced high or low against similar projects '
            "nearby, and what might a unit be worth?\n\n"
            "1. Call search_projects to get the exact project name. If it is ambiguous, or the "
            "only match has match_type 'fuzzy', ask me which project I mean first.\n"
            "2. Call market_activity for the project (period quarter) to see how liquid it is and "
            "whether sales volume is rising or falling.\n"
            f"3. Call compare_nearby for the project (with {area_arg} if I gave one). Choose "
            "type_of_sale sensibly and say so: new launch and resale prices differ.\n"
            "4. If the notes say peers are much older or newer, or have a different tenure type, "
            "call compare_nearby again with lease_year_tolerance=10 and/or same_tenure_type=true "
            "and compare the two answers.\n"
            f"5. If I gave or you have a unit size, call estimate_value with {area_arg}"
            " (and floor and type_of_sale if known). Show the range, not just the median. Compare "
            "it with comparable_check, and say if the notes warn of a small sample, weak fit, "
            "extrapolation, or area mode (the project has too few sales of its own).\n"
            "6. Call price_trend for the project (period quarter) to see whether its price is "
            "rising or falling.\n"
            "7. Report: the project's median psf versus the peer median and premium_pct, the "
            "peers with distance and psf, the estimated range for the unit, the liquidity and "
            "price trend, and every caveat from the notes.\n"
            "Prices are SGD and areas are sqft. These are statistical comparisons of recorded "
            "prices, not valuations: say what they ignore (view, facing, renovation, the exact "
            "unit). Quote only figures from the tools."
        )
