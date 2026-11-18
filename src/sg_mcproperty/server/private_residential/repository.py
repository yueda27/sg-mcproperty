"""Data access for private residential transactions: SQL and parameters, no business rules.

Table (`sg_property.private_residential_transactions`): plain MergeTree, so no FINAL is needed.
Prices are SGD, areas are sqft, `contract_date` is the first day of the month, and `floor` and
`lease_year` are nullable.

SQL lives in `string.Template` constants. Only the trusted table name and fixed SQL fragments
are substituted with `$name`. Every value goes through a `{name:Type}` parameter.
"""

from datetime import date
from string import Template
from typing import Any

from sg_mcproperty.accessor import Queryable

from .filters import (
    SEGMENTS,
    AffordabilityQuery,
    GrowthQuery,
    PeerFilters,
    TransactionFilters,
)

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

# Median price and psf per period. `$period_fn` is one of the whitelisted functions in
# `filters.PERIODS`. `$where` always includes `area_sqft > 0` so psf is defined.
PRICE_TREND_SQL = Template(
    """
    SELECT
        $period_fn(contract_date) AS period,
        count() AS transactions,
        round(quantileExact(0.5)(price)) AS median_price,
        round(quantileExact(0.5)(price / area_sqft)) AS median_psf
    FROM $table
    $where
    GROUP BY period
    ORDER BY period
    LIMIT 200
    """
)

# One row describing a project: where it is, what it is, and its latest tenure. x and y are
# averaged because multi-block projects can carry several coordinates (null if none known).
PROJECT_PROFILE_SQL = Template(
    """
    SELECT
        project,
        any(street) AS street,
        argMax(district, contract_date) AS district,
        argMax(property_type, contract_date) AS property_type,
        argMax(tenure, contract_date) AS tenure,
        argMax(lease_year, contract_date) AS lease_year,
        count() AS transactions,
        avg(x) AS x,
        avg(y) AS y
    FROM $table
    WHERE project = {project:String}
    GROUP BY project
    """
)

# Output aliases are prefixed `peer_` because an alias that equals a column name would shadow
# the column inside WHERE (ClickHouse then rejects the aggregate). `_unprefix` strips them.
_PEER_COLUMNS = """
        project,
        any(street) AS peer_street,
        argMax(district, contract_date) AS peer_district,
        argMax(property_type, contract_date) AS peer_property_type,
        argMax(tenure, contract_date) AS peer_tenure,
        argMax(lease_year, contract_date) AS peer_lease_year,
        count() AS transactions,
        round(quantileExact(0.5)(price / area_sqft)) AS median_psf,
        round(quantileExact(0.5)(price)) AS median_price,
"""

# Peers by distance (projects with coordinates). `$peer_filters` comes from PeerFilters.
PEERS_RADIUS_SQL = Template(
    "SELECT"
    + _PEER_COLUMNS
    + """
        round(sqrt(pow(avg(x) - {target_x:Float64}, 2) + pow(avg(y) - {target_y:Float64}, 2)))
            AS distance_m
    FROM $table
    WHERE project != {project:String} AND x IS NOT NULL AND area_sqft > 0 $peer_filters
    GROUP BY project
    HAVING distance_m <= {radius_m:Float64} AND transactions >= {min_transactions:UInt32}
    ORDER BY distance_m ASC
    LIMIT {limit:UInt32}
    """
)

# Fallback when the target has no coordinates: projects in the same street or district,
# busiest first. `$area_condition` is one of AREA_CONDITIONS.
PEERS_AREA_SQL = Template(
    "SELECT"
    + _PEER_COLUMNS
    + """
        NULL AS distance_m
    FROM $table
    WHERE project != {project:String} AND $area_condition AND area_sqft > 0
        $peer_filters
    GROUP BY project
    HAVING transactions >= {min_transactions:UInt32}
    ORDER BY transactions DESC
    LIMIT {limit:UInt32}
    """
)

AREA_CONDITIONS = {
    "street": "street = {area_value:String}",
    "district": "district = {area_value:UInt8}",
}

# psf and price distribution over a set of transactions: the target project (`project = ...`)
# or the pooled transactions of the chosen peers (`project IN ...`).
STATS_SQL = Template(
    """
    SELECT
        count() AS transactions,
        uniqExact(project) AS projects,
        round(quantileExact(0.25)(price / area_sqft)) AS p25_psf,
        round(quantileExact(0.5)(price / area_sqft)) AS median_psf,
        round(quantileExact(0.75)(price / area_sqft)) AS p75_psf,
        round(quantileExact(0.5)(price)) AS median_price
    FROM $table
    WHERE $project_condition AND area_sqft > 0 $peer_filters
    """
)
TARGET_CONDITION = "project = {project:String}"
PEERS_CONDITION = "project IN {projects:Array(String)}"


# What values exist: lets a client choose valid filters and judge how fresh and complete the data is.
DATASET_FACETS_SQL = Template(
    """
    SELECT
        arraySort(groupUniqArray(district)) AS districts,
        arraySort(groupUniqArray(type_of_sale)) AS types_of_sale,
        round(100 * uniqExactIf(project, x IS NOT NULL) / uniqExact(project), 1)
            AS projects_with_coordinates_pct,
        round(100 * countIf(x IS NOT NULL) / count(), 1) AS transactions_with_coordinates_pct
    FROM $table
    """
)

PROPERTY_TYPE_COUNTS_SQL = Template(
    """
    SELECT property_type, count() AS transactions
    FROM $table
    GROUP BY property_type
    ORDER BY transactions DESC
    """
)

# Everything about one project in a single pass: all-time ranges and the psf of two consecutive
# windows (recent and prior). Conditions inside *If aggregates keep one row per project.
PROJECT_SUMMARY_SQL = Template(
    """
    SELECT
        count() AS transactions,
        min(contract_date) AS first_sale,
        max(contract_date) AS last_sale,
        countIf(type_of_sale = 'New Sale') AS new_sales,
        countIf(type_of_sale = 'Sub Sale') AS sub_sales,
        countIf(type_of_sale = 'Resale') AS resales,
        min(price) AS min_price,
        round(quantileExact(0.5)(price)) AS median_price,
        max(price) AS max_price,
        round(quantileExactIf(0.25)(price / area_sqft, area_sqft > 0)) AS p25_psf,
        round(quantileExactIf(0.5)(price / area_sqft, area_sqft > 0)) AS median_psf,
        round(quantileExactIf(0.75)(price / area_sqft, area_sqft > 0)) AS p75_psf,
        round(min(area_sqft), 1) AS min_area_sqft,
        round(quantileExact(0.5)(area_sqft), 1) AS median_area_sqft,
        round(max(area_sqft), 1) AS max_area_sqft,
        countIf(contract_date >= {recent_start:Date}) AS recent_transactions,
        round(quantileExactIf(0.5)(
            price / area_sqft, contract_date >= {recent_start:Date} AND area_sqft > 0
        )) AS recent_median_psf,
        countIf(contract_date >= {prior_start:Date} AND contract_date < {recent_start:Date})
            AS prior_transactions,
        round(quantileExactIf(0.5)(
            price / area_sqft,
            contract_date >= {prior_start:Date} AND contract_date < {recent_start:Date}
                AND area_sqft > 0
        )) AS prior_median_psf
    FROM $table
    WHERE project = {project:String}
    """
)


# Monthly (or quarterly, yearly) counts split by sale type, with median price and psf.
MARKET_ACTIVITY_SQL = Template(
    """
    SELECT
        $period_fn(contract_date) AS period,
        count() AS transactions,
        countIf(type_of_sale = 'New Sale') AS new_sales,
        countIf(type_of_sale = 'Sub Sale') AS sub_sales,
        countIf(type_of_sale = 'Resale') AS resales,
        round(quantileExact(0.5)(price)) AS median_price,
        round(quantileExactIf(0.5)(price / area_sqft, area_sqft > 0)) AS median_psf
    FROM $table
    $where
    GROUP BY period
    ORDER BY period
    LIMIT 200
    """
)

# Raw sales of the chosen projects, the input to the valuation regression. Bounded by LIMIT.
VALUATION_SAMPLE_SQL = Template(
    """
    SELECT project, area_sqft, floor, price, contract_date, type_of_sale, tenure, lease_year
    FROM $table
    WHERE project IN {projects:Array(String)} AND area_sqft > 0 AND price > 0 $peer_filters
    ORDER BY contract_date DESC
    LIMIT {limit:UInt32}
    """
)

# Tenure filters for the growth scan. Fixed fragments, chosen from a whitelist in filters.py.
# The table alias `t` keeps an output alias such as `tenure` from shadowing the raw column.
GROWTH_TENURE_SQL = {
    "any": "",
    "freehold": "AND t.tenure = 'Freehold'",
    "999 years": "AND t.tenure = '999 years'",
    "freehold or 999 years": "AND t.tenure IN ('Freehold', '999 years')",
}

# One project's psf growth: log psf regressed on time for its resales in the size band.
# `$area_filter` holds fixed fragments with parameterised bounds.
PROJECT_GROWTH_SQL = Template(
    """
    WITH bounds AS (SELECT max(contract_date) AS last_month FROM $table),
    sales AS (
        SELECT
            price / area_sqft AS psf,
            price,
            dateDiff('day', contract_date, (SELECT last_month FROM bounds)) / 365.25
                AS years_before_end
        FROM $table
        WHERE project = {project:String} AND area_sqft > 0 AND price > 0
            AND type_of_sale IN ('Resale', 'Sub Sale')
            AND contract_date >= addMonths(
                (SELECT last_month FROM bounds), -({months:Int32} - 1)
            )
            $area_filter
    )
    SELECT
        count() AS transactions,
        round(quantileExactIf(0.5)(psf, psf > 0)) AS median_psf,
        round(quantileExactIf(0.5)(price, psf > 0)) AS median_price,
        if(count() > 2 AND varPop(-years_before_end) > 0,
            exp(covarPop(ln(psf), -years_before_end) / varPop(-years_before_end)) - 1,
            NULL) AS annual_growth
    FROM sales
    """
)

# The nearest projects (by SVY21 distance), each with its own psf growth over the same window
# and size band. Growth is null when the project has fewer than 3 resales in the band.
NEARBY_GROWTH_SQL = Template(
    """
    WITH bounds AS (SELECT max(contract_date) AS last_month FROM $table),
    near AS (
        SELECT
            project,
            any(street) AS street,
            argMax(district, contract_date) AS district,
            argMax(tenure, contract_date) AS tenure,
            argMax(lease_year, contract_date) AS lease_year,
            argMax(property_type, contract_date) AS property_type,
            round(sqrt(
                pow(avg(t.x) - {target_x:Float64}, 2) + pow(avg(t.y) - {target_y:Float64}, 2)
            )) AS distance_m
        FROM $table t
        WHERE t.project != {project:String} AND t.x IS NOT NULL AND t.area_sqft > 0
            AND t.property_type IN {property_types:Array(String)}
            $tenure_filter
        GROUP BY t.project
        HAVING distance_m <= {radius_m:Float64}
        ORDER BY distance_m ASC
        LIMIT {max_peers:UInt32}
    ),
    sales AS (
        SELECT
            project,
            count() AS transactions,
            round(quantileExactIf(0.5)(psf, psf > 0)) AS median_psf,
            round(quantileExactIf(0.5)(price, psf > 0)) AS median_price,
            if(count() > 2 AND varPop(-years_before_end) > 0,
                exp(covarPop(ln(psf), -years_before_end) / varPop(-years_before_end)) - 1,
                NULL) AS annual_growth
        FROM (
            SELECT
                t.project AS project,
                t.price / t.area_sqft AS psf,
                t.price AS price,
                dateDiff('day', t.contract_date, (SELECT last_month FROM bounds)) / 365.25
                    AS years_before_end
            FROM $table t
            INNER JOIN near n ON t.project = n.project
            WHERE t.area_sqft > 0
                AND t.price > 0
                AND t.type_of_sale IN ('Resale', 'Sub Sale')
                AND t.contract_date >= addMonths(
                    (SELECT last_month FROM bounds), -({months:Int32} - 1)
                )
                $area_filter
        )
        GROUP BY project
    )
    SELECT
        n.project,
        n.street,
        n.district,
        n.tenure,
        n.lease_year,
        n.property_type,
        n.distance_m,
        if(s.project = '', 0, s.transactions) AS transactions,
        if(s.project = '', NULL, s.median_psf) AS median_psf,
        if(s.project = '', NULL, s.median_price) AS median_price,
        if(s.project = '', NULL, s.annual_growth) AS annual_growth
    FROM near n
    LEFT JOIN sales s ON s.project = n.project
    ORDER BY isNull(annual_growth) ASC, annual_growth DESC, n.distance_m ASC
    """
)

# Projects with at least one sale inside the budget. `$where` comes from TransactionFilters.
# Filter in a subquery so the output aliases (district, transactions, ...) cannot shadow the
# columns the WHERE clause filters on.
AFFORDABLE_PROJECTS_SQL = Template(
    """
    SELECT
        project,
        any(street) AS street,
        any(district) AS district,
        count() AS transactions,
        round(quantileExact(0.5)(price)) AS median_price,
        min(price) AS min_price,
        round(quantileExactIf(0.5)(price / area_sqft, area_sqft > 0)) AS median_psf,
        round(min(area_sqft), 1) AS min_area_sqft,
        round(max(area_sqft), 1) AS max_area_sqft
    FROM (
        SELECT * FROM $table
        $where
    )
    GROUP BY project
    HAVING transactions >= {min_transactions:UInt32}
    ORDER BY median_price DESC
    LIMIT {limit:UInt32}
    """
)


# --- Repository (data access) -------------------------------------------------------------------


def _unprefix(row: dict[str, Any]) -> dict[str, Any]:
    return {k.removeprefix("peer_"): v for k, v in row.items()}


def _area_filter(
    min_area: float | None, max_area: float | None, prefix: str = ""
) -> tuple[str, dict[str, Any]]:
    """Fixed area fragments plus parameter values, for the growth queries."""
    sql, params = "", {}
    if min_area is not None:
        sql += f"AND {prefix}area_sqft >= {{min_area_sqft:Float64}} "
        params["min_area_sqft"] = min_area
    if max_area is not None:
        sql += f"AND {prefix}area_sqft <= {{max_area_sqft:Float64}} "
        params["max_area_sqft"] = max_area
    return sql, params


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

    def price_trend(self, filters: TransactionFilters, period_fn: str) -> list[dict[str, Any]]:
        where, params = filters.to_sql()
        where = f"{where} AND area_sqft > 0" if where else "WHERE area_sqft > 0"
        sql = PRICE_TREND_SQL.substitute(table=self._table, where=where, period_fn=period_fn)
        return self._accessor.query(sql, params)

    def project_profile(self, project: str) -> dict[str, Any] | None:
        sql = PROJECT_PROFILE_SQL.substitute(table=self._table)
        rows = self._accessor.query(sql, {"project": project})
        return rows[0] if rows else None

    def find_peers_by_distance(
        self,
        project: str,
        x: float,
        y: float,
        radius_m: float,
        filters: PeerFilters,
        min_transactions: int,
        limit: int,
    ) -> list[dict[str, Any]]:
        peer_sql, params = filters.to_sql()
        sql = PEERS_RADIUS_SQL.substitute(table=self._table, peer_filters=peer_sql)
        rows = self._accessor.query(
            sql,
            {
                **params,
                "project": project,
                "target_x": x,
                "target_y": y,
                "radius_m": radius_m,
                "min_transactions": min_transactions,
                "limit": limit,
            },
        )
        return [_unprefix(row) for row in rows]

    def find_peers_by_area(
        self,
        project: str,
        scope: str,
        value: str | int,
        filters: PeerFilters,
        min_transactions: int,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Peers in the same `scope` ("street" or "district"), for projects without coordinates."""
        peer_sql, params = filters.to_sql()
        sql = PEERS_AREA_SQL.substitute(
            table=self._table, peer_filters=peer_sql, area_condition=AREA_CONDITIONS[scope]
        )
        rows = self._accessor.query(
            sql,
            {
                **params,
                "project": project,
                "area_value": value,
                "min_transactions": min_transactions,
                "limit": limit,
            },
        )
        return [_unprefix(row) for row in rows]

    def project_stats(self, project: str, filters: PeerFilters) -> dict[str, Any]:
        return self._stats(TARGET_CONDITION, {"project": project}, filters)

    def pooled_stats(self, projects: list[str], filters: PeerFilters) -> dict[str, Any]:
        return self._stats(PEERS_CONDITION, {"projects": projects}, filters)

    def _stats(
        self, condition: str, params: dict[str, Any], filters: PeerFilters
    ) -> dict[str, Any]:
        peer_sql, filter_params = filters.to_sql()
        sql = STATS_SQL.substitute(
            table=self._table, project_condition=condition, peer_filters=peer_sql
        )
        return self._accessor.query(sql, {**filter_params, **params})[0]

    def dataset_facets(self) -> dict[str, Any]:
        return self._accessor.query(DATASET_FACETS_SQL.substitute(table=self._table))[0]

    def property_type_counts(self) -> list[dict[str, Any]]:
        return self._accessor.query(PROPERTY_TYPE_COUNTS_SQL.substitute(table=self._table))

    def project_summary(
        self, project: str, recent_start: date, prior_start: date
    ) -> dict[str, Any]:
        sql = PROJECT_SUMMARY_SQL.substitute(table=self._table)
        params = {
            "project": project,
            "recent_start": recent_start.isoformat(),
            "prior_start": prior_start.isoformat(),
        }
        return self._accessor.query(sql, params)[0]

    def market_activity(self, filters: TransactionFilters, period_fn: str) -> list[dict[str, Any]]:
        where, params = filters.to_sql()
        sql = MARKET_ACTIVITY_SQL.substitute(table=self._table, where=where, period_fn=period_fn)
        return self._accessor.query(sql, params)

    def valuation_sample(
        self, projects: list[str], filters: PeerFilters, limit: int
    ) -> list[dict[str, Any]]:
        peer_sql, params = filters.to_sql()
        sql = VALUATION_SAMPLE_SQL.substitute(table=self._table, peer_filters=peer_sql)
        return self._accessor.query(sql, {**params, "projects": projects, "limit": limit})

    def nearby_growth(self, query: GrowthQuery, x: float, y: float) -> list[dict[str, Any]]:
        area_sql, area_params = _area_filter(query.min_area_sqft, query.max_area_sqft, prefix="t.")
        sql = NEARBY_GROWTH_SQL.substitute(
            table=self._table,
            tenure_filter=GROWTH_TENURE_SQL[query.tenure],
            area_filter=area_sql,
        )
        return self._accessor.query(
            sql,
            {
                **area_params,
                "project": query.project,
                "target_x": x,
                "target_y": y,
                "property_types": list(SEGMENTS[query.segment]),
                "radius_m": query.radius_m,
                "max_peers": query.max_peers,
                "months": query.months,
            },
        )

    def project_growth(self, query: GrowthQuery) -> dict[str, Any]:
        area_sql, area_params = _area_filter(query.min_area_sqft, query.max_area_sqft)
        sql = PROJECT_GROWTH_SQL.substitute(table=self._table, area_filter=area_sql)
        return self._accessor.query(
            sql,
            {**area_params, "project": query.project, "months": query.months},
        )[0]

    def affordable_projects(self, query: AffordabilityQuery) -> list[dict[str, Any]]:
        filters = TransactionFilters.from_inputs(
            district=query.district,
            max_price=query.max_price,
            min_area_sqft=query.min_area_sqft,
            segment=query.segment,
        )
        where, params = filters.to_sql()
        where = f"{where} AND area_sqft > 0" if where else "WHERE area_sqft > 0"
        sql = AFFORDABLE_PROJECTS_SQL.substitute(table=self._table, where=where)
        return self._accessor.query(
            sql,
            {
                **params,
                "min_transactions": query.min_transactions,
                "limit": query.limit,
            },
        )

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
