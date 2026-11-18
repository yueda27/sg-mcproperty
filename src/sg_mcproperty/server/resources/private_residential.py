"""MCP resources for private residential data: reference material a client can read.

Resources are read by the client or user rather than called by the model, so they suit
documentation and small status documents.
"""

import json

from fastmcp import FastMCP

from sg_mcproperty.server.private_residential import PrivateResidentialService

SCHEMA_DOC = """\
# Private residential transactions

Source: URA private residential transactions, last 5 years. One row per transaction.

| Column | Meaning |
|---|---|
| project | Project name, upper case (use search_projects to find the exact name) |
| street | Street of the project |
| district | Postal district, 1-28 |
| contract_date | First day of the month. Sale month for New Sale, option exercised month for Resale and Sub Sale |
| type_of_sale | New Sale, Sub Sale or Resale |
| price | SGD |
| area_sqft | Area in square feet (converted from URA square metres) |
| psf | Price per square foot in SGD, price / area_sqft |
| floor | Midpoint of the URA floor range (06-10 becomes 8). Null for landed property |
| tenure | Length of tenure, e.g. "99 years", "999 years", or "Freehold" |
| lease_year | Lease commencement year. Null for freehold |
| property_type | URA type: Apartment, Condominium, Executive Condominium, Terrace, Semi-detached, Detached and the Strata variants of the three landed types |
| x, y | Project location in SVY21 metres (distance between projects is Euclidean). Null for many projects |

Segments group property types so psf is comparable inside a group:
- non-landed: Apartment, Condominium
- ec: Executive Condominium
- landed: Terrace, Semi-detached, Detached (psf is on land area)
- strata-landed: Strata Terrace, Strata Semi-detached, Strata Detached (psf is on floor area)

Caveats:
- URA gives month precision only, so there is no sale day.
- Landed property has no floor and its psf is not comparable with condos.
- There is no bedroom count in the data.
- About 5% of projects, but 22% of transactions, have no coordinates. These are mostly recent
  new launches, so "nearby" comparisons fall back to the same street, then the same district.
"""


def register(mcp: FastMCP, service: PrivateResidentialService) -> None:
    """Register private residential resources on `mcp`."""

    @mcp.resource("property://private-residential/schema", mime_type="text/markdown")
    def schema() -> str:
        """Column meanings, units and caveats for the private residential dataset."""
        return SCHEMA_DOC

    @mcp.resource("property://private-residential/coverage", mime_type="application/json")
    def coverage() -> str:
        """What the loaded private residential data covers: row count, projects, date range."""
        return json.dumps(service.coverage())
