"""Tools over `sg_property.private_residential_transactions`.

Planned tools (see server/CLAUDE.md for the rules each one must follow: read-only client,
`{name:Type}` parameters, bounded results, precise docstrings with units and valid filter
values):

- search_projects: DONE, resolve a fuzzy name to exact project names.
- search_transactions: filtered recent transactions, capped by a `limit`.
- price_trend: monthly median price and price per sqft, filtered by district or project.
- project_summary: stats for one project (count, median price, psf, tenure, lease_year).
- district_comparison: compare districts on median psf over a period.

Table notes: it is a plain MergeTree (no FINAL needed), prices are SGD, areas are sqft,
`contract_date` is the first day of the month, `floor` and `lease_year` are nullable.

SQL lives in `string.Template` constants. Only the trusted table name and a fixed filter
fragment are substituted with `$name`. Every value goes through a `{name:Type}` parameter.
"""

from datetime import datetime
from string import Template
from typing import Any, Protocol

from fastmcp import FastMCP

from sg_mcproperty.accessor import ClickHouseAccessor

TABLE = "private_residential_transactions"
MAX_PROJECT_RESULTS = 25
MIN_QUERY_CHARS = 2

DISTRICT_FILTER = "AND district = {district:UInt8}"

# Every query token must appear in the project name. Rank exact, then prefix, then contains,
# then the busiest project first.
SEARCH_PROJECTS_SQL = Template(
    """
    SELECT
        project,
        any(street) AS street,
        district,
        argMax(tenure, contract_date) AS tenure,
        argMax(lease_year, contract_date) AS lease_year,
        count() AS transactions,
        min(contract_date) AS first_sale,
        max(contract_date) AS last_sale,
        multiIf(
            project = {query:String}, 'exact',
            startsWith(project, {query:String}), 'prefix',
            'contains'
        ) AS match_type
    FROM $table
    WHERE arrayAll(t -> positionCaseInsensitive(project, t) > 0, {tokens:Array(String)})
    $district_filter
    GROUP BY project, district
    ORDER BY multiIf(match_type = 'exact', 0, match_type = 'prefix', 1, 2), transactions DESC
    LIMIT {limit:UInt32}
    """
)

# Typo tolerant fallback, used only when the token match finds nothing. Each query token must
# be within a few edits of some word in the project name: 0 edits for tokens under 4 chars,
# 1 for 4-7 chars, 2 for 8+ (Damerau-Levenshtein, so a transposition counts as one edit).
SEARCH_PROJECTS_FUZZY_SQL = Template(
    """
    SELECT
        project,
        any(street) AS street,
        district,
        argMax(tenure, contract_date) AS tenure,
        argMax(lease_year, contract_date) AS lease_year,
        count() AS transactions,
        min(contract_date) AS first_sale,
        max(contract_date) AS last_sale,
        'fuzzy' AS match_type,
        arraySum(
            t -> arrayMin(arrayMap(w -> damerauLevenshteinDistance(w, t), splitByChar(' ', project))),
            {tokens:Array(String)}
        ) AS edits
    FROM $table
    WHERE arrayAll(
        t -> arrayMin(arrayMap(w -> damerauLevenshteinDistance(w, t), splitByChar(' ', project)))
            <= intDiv(length(t), 4),
        {tokens:Array(String)}
    )
    $district_filter
    GROUP BY project, district
    ORDER BY edits ASC, transactions DESC
    LIMIT {limit:UInt32}
    """
)


class Queryable(Protocol):
    def table(self, name: str) -> str: ...

    def query(self, sql: str, parameters: dict[str, Any] | None = None) -> list[dict[str, Any]]: ...


def normalise_query(name_query: str) -> tuple[str, list[str]]:
    """Upper-case and whitespace-normalise a project name query. Returns (query, tokens)."""
    query = " ".join(name_query.upper().split())
    if len(query) < MIN_QUERY_CHARS:
        raise ValueError(f"name_query must be at least {MIN_QUERY_CHARS} characters")
    return query, query.split(" ")


def _clean(row: dict[str, Any]) -> dict[str, Any]:
    """Make a result row JSON friendly and drop internal columns."""
    row = {k: v for k, v in row.items() if k != "edits"}
    for key in ("first_sale", "last_sale"):
        if isinstance(row[key], datetime):
            row[key] = row[key].date().isoformat()
    return row


def search_projects(
    accessor: Queryable,
    name_query: str,
    district: int | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    query, tokens = normalise_query(name_query)
    params: dict[str, Any] = {
        "query": query,
        "tokens": tokens,
        "limit": max(1, min(limit, MAX_PROJECT_RESULTS)),
    }
    district_filter = ""
    if district is not None:
        params["district"] = district
        district_filter = DISTRICT_FILTER

    table = accessor.table(TABLE)
    rows = accessor.query(
        SEARCH_PROJECTS_SQL.substitute(table=table, district_filter=district_filter), params
    )
    if not rows:
        rows = accessor.query(
            SEARCH_PROJECTS_FUZZY_SQL.substitute(table=table, district_filter=district_filter),
            params,
        )
    return [_clean(r) for r in rows]


def register(mcp: FastMCP, accessor: Queryable | None = None) -> None:
    """Register private residential tools on `mcp`."""
    db = accessor or ClickHouseAccessor()

    @mcp.tool(name="search_projects")
    def search_projects_tool(
        name_query: str, district: int | None = None, limit: int = 10
    ) -> list[dict[str, Any]]:
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
        return search_projects(db, name_query, district, limit)
