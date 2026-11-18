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
            "2. Call search_transactions for that exact project, newest first, and look at the "
            "most recent sales.\n"
            "3. Summarise: how many sales are on record (total_matches), the price and psf range "
            "of the latest sales, the mix of New Sale versus Resale, and the tenure and lease "
            "start year.\n"
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
            "private-residential coverage resource if you need the latest month.\n"
            f"2. Call search_transactions with district={district} and that start_date.\n"
            "3. Report total_matches, then the highest-priced sales with project, price, area "
            "and psf. If total_matches is larger than what was returned, say the list is "
            "partial.\n"
            "Prices are SGD and areas are sqft. Do not compare landed property (no floor) with "
            "condos on psf."
        )
