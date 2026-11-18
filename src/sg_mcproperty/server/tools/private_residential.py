"""Tools over `sg_property.private_residential_transactions`.

Scaffold only: no tools are registered yet. Planned tools (see server/CLAUDE.md for the rules
each one must follow: read-only client, `{name:Type}` parameters, bounded results, precise
docstrings with units and valid filter values):

- search_transactions: filtered recent transactions (project, district, type_of_sale,
  date range, price and area bounds), capped by a `limit`.
- price_trend: monthly median price and price per sqft, filtered by district or project.
- project_summary: stats for one project (count, median price, psf, tenure, lease_year).
- district_comparison: compare districts on median psf over a period.

Table notes: it is a plain MergeTree (no FINAL needed), prices are SGD, areas are sqft,
`contract_date` is the first day of the month, `floor` and `lease_year` are nullable.
"""

from fastmcp import FastMCP


def register(mcp: FastMCP) -> None:
    """Register private residential tools on `mcp`. Nothing to register yet."""
    # TODO: define tools here with @mcp.tool, using get_client(readonly=True).
