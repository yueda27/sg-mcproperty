"""MCP prompts for private residential data: reusable starting points a user can pick.

A prompt returns instructions for the model, telling it which tools to use and how to present
the answer. It does not fetch data itself. Only reference tools that exist.
"""

from fastmcp import FastMCP


def register(mcp: FastMCP) -> None:
    """Register private residential prompts on `mcp`."""

    @mcp.prompt
    def project_overview(project_name: str) -> str:
        """Summarise recent sales and price momentum at one project."""
        return (
            f"Give me an overview of the private residential project "
            f'"{project_name}".\n\n'
            "1. Call search_projects with this name to get the exact project name. If there are "
            "several matches, or the only match has match_type 'fuzzy', ask me which one I mean "
            "before continuing.\n"
            "2. Call project_summary for that exact project for its profile: sales mix, price and "
            "psf ranges, tenure and lease start, and the latest 12 months versus the 12 before.\n"
            "3. Call market_activity for the project with period='quarter' to see how many sales "
            "there are over time and whether volume is rising or falling. Say that the latest "
            "period may be incomplete because URA publishes with a delay.\n"
            "4. Call price_trend for the project with period='quarter' to see whether psf is "
            "rising or falling. psf moves with unit size, so when the project's units vary a lot "
            "(check area_sqft in project_summary) add min_area_sqft and max_area_sqft around the "
            "typical area. Treat low_sample periods as noise and use psf_change_pct only when it "
            "is not null.\n"
            "5. If I want examples, call search_transactions for the project (newest first, "
            "limit up to 100).\n"
            "Summarise: how many sales are on record, the price and psf range, the mix of New Sale "
            "versus Resale, the tenure and lease start year, the sales volume, and whether psf is "
            "rising or falling. Prices are SGD and areas are sqft. Quote figures only from the "
            "tool results, and say when the sample is small or a period is low_sample."
        )

    @mcp.prompt
    def recent_deals(district: int, months: int = 6) -> str:
        """List the biggest recent transactions in a postal district."""
        return (
            f"Show me notable private residential transactions in postal district {district} "
            f"over the last {months} months.\n\n"
            "1. Call dataset_info for the latest month in the data, then work out the start "
            f"month (YYYY-MM) {months} months before it.\n"
            f"2. Call search_transactions with district={district} and that start_date. Results "
            "are newest first, never sorted by price, and limit is capped at 100 (default 20).\n"
            "3. To find the biggest sales, either raise min_price so total_matches fits within "
            "the limit, or fetch limit=100 and sort those rows by price yourself. Say which you "
            "did.\n"
            "4. Report total_matches, then the highest-priced sales with project, contract_date, "
            "price, area and psf. If total_matches is larger than returned, say the ranking is "
            "partial and only covers the rows fetched. Add segment (for example 'non-landed' for "
            "condos and apartments) if I ask for a property group.\n"
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
            "2. Call market_activity for the project with period='quarter' to see how liquid it "
            "is and whether sales volume is rising or falling. Note that the latest period may be "
            "incomplete.\n"
            f"3. Call compare_nearby for the project (with {area_arg} if I gave one). Choose "
            "type_of_sale sensibly and say so: new launch and resale prices differ. It uses the "
            "nearest peers (max_peers up to 50), the latest months of sales (up to 60; raise it "
            "when 12 months is thin, and say which window you used), and a size band of +/-20% "
            "around area_sqft if I gave one.\n"
            "4. If the notes say peers are much older or newer, or have a different tenure type, "
            "call compare_nearby again with lease_year_tolerance=10 and/or same_tenure_type=true "
            "and compare the two answers. Widen max_peers (up to 50) when you need a fuller peer "
            "set, and say when the peer list is capped.\n"
            f"5. If I gave or you have a unit size, call estimate_value with {area_arg}"
            " (and floor and type_of_sale if known). It also uses nearby projects: raise months "
            "(up to 60) for a longer market trend, or radius_m (up to 3000) and max_peers (up to "
            "50) when the area is thin. "
            "Show the median and the low-high range (an 80% interval), not just the median. "
            "Report model.mode (project or area), sales_used, projects and typical_error_pct, "
            "compare the median with comparable_check, and say if the notes warn of a small "
            "sample, weak fit, extrapolation, or area mode (the project has too few sales of its "
            "own). Mention monthly_trend_pct and any freehold or new-sale premium learned when "
            "they are not null.\n"
            "6. Call price_trend for the project with period='quarter' to see whether its price "
            "is rising or falling. Add min_area_sqft and max_area_sqft for a like-for-like size "
            "band when the project's units vary.\n"
            "7. Report: the project's median psf versus the peer median and premium_pct, the "
            "peers with distance and psf, the indicative_value range from compare_nearby if a "
            "size was given, the estimated range for the unit, the liquidity and price trend, and "
            "every caveat from the notes.\n"
            "Prices are SGD and areas are sqft. These are statistical comparisons of recorded "
            "prices, not valuations: say what they ignore (view, facing, renovation, the exact "
            "unit). Quote only figures from the tools."
        )
