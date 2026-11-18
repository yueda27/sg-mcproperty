"""Data access for private residential transactions: SQL and parameters, no business rules.

Table (`sg_property.private_residential_transactions`): plain MergeTree, so no FINAL is needed.
Prices are SGD, areas are sqft, `contract_date` is the first day of the month, and `floor` and
`lease_year` are nullable.

SQL lives in `string.Template` constants. Only the trusted table name and fixed SQL fragments
are substituted with `$name`. Every value goes through a `{name:Type}` parameter.
"""

from string import Template
from typing import Any

from sg_mcproperty.accessor import Queryable

from .filters import TransactionFilters

TABLE = "private_residential_transactions"

DISTRICT_FILTER = "AND district = {district:UInt8}"

# Every query word must appear in the project name. Rank exact, then prefix, then contains,
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

# Typo tolerant fallback, used only when the word match finds nothing. Each query word must be
# within a few edits of some word in the project name: 0 edits for words under 4 chars, 1 for
# 4-7 chars, 2 for 8+ (Damerau-Levenshtein, so a transposition counts as one edit).
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

# Raw transactions, newest first. `$where` comes from `TransactionFilters.to_sql`.
SEARCH_TRANSACTIONS_SQL = Template(
    """
    SELECT
        project,
        street,
        district,
        contract_date,
        type_of_sale,
        price,
        round(area_sqft, 1) AS area_sqft,
        if(area_sqft > 0, round(price / area_sqft), NULL) AS psf,
        floor,
        tenure,
        lease_year
    FROM $table
    $where
    ORDER BY contract_date DESC, price DESC
    LIMIT {limit:UInt32}
    """
)

COUNT_TRANSACTIONS_SQL = Template("SELECT count() AS total FROM $table $where")

COVERAGE_SQL = Template(
    """
    SELECT
        count() AS transactions,
        uniqExact(project) AS projects,
        min(contract_date) AS first_month,
        max(contract_date) AS last_month
    FROM $table
    """
)


# --- Repository (data access) -------------------------------------------------------------------


class PrivateResidentialRepository:
    """Runs the queries and returns raw rows. Knows SQL, not business rules."""

    def __init__(self, accessor: Queryable) -> None:
        self._accessor = accessor

    def find_projects(
        self, query: str, tokens: list[str], district: int | None, limit: int
    ) -> list[dict[str, Any]]:
        return self._run_project_search(SEARCH_PROJECTS_SQL, query, tokens, district, limit)

    def find_projects_fuzzy(
        self, query: str, tokens: list[str], district: int | None, limit: int
    ) -> list[dict[str, Any]]:
        return self._run_project_search(SEARCH_PROJECTS_FUZZY_SQL, query, tokens, district, limit)

    def count_transactions(self, filters: TransactionFilters) -> int:
        where, params = filters.to_sql()
        sql = COUNT_TRANSACTIONS_SQL.substitute(table=self._table, where=where)
        return self._accessor.query(sql, params)[0]["total"]

    def list_transactions(self, filters: TransactionFilters, limit: int) -> list[dict[str, Any]]:
        where, params = filters.to_sql()
        sql = SEARCH_TRANSACTIONS_SQL.substitute(table=self._table, where=where)
        return self._accessor.query(sql, {**params, "limit": limit})

    def coverage(self) -> dict[str, Any]:
        return self._accessor.query(COVERAGE_SQL.substitute(table=self._table))[0]

    @property
    def _table(self) -> str:
        return self._accessor.table(TABLE)

    def _run_project_search(
        self,
        template: Template,
        query: str,
        tokens: list[str],
        district: int | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"query": query, "tokens": tokens, "limit": limit}
        district_filter = ""
        if district is not None:
            params["district"] = district
            district_filter = DISTRICT_FILTER
        sql = template.substitute(table=self._table, district_filter=district_filter)
        return self._accessor.query(sql, params)
