"""Data access for private residential rentals: SQL and parameters, no business rules.

Table (`sg_property.private_residential_rentals`): plain MergeTree, so no FINAL is needed. URA
reports rented floor area as a *band* ("1700-1800", ">3000"), so rent per sqft uses the band
midpoint, and the open top band falls back to its lower bound (making its psf an upper bound).

SQL lives in `string.Template` constants. Only the trusted table name and fixed SQL fragments
are substituted with `$name`. Every value goes through a `{name:Type}` parameter.
"""

from string import Template
from typing import Any

from sg_mcproperty.accessor import Queryable

TABLE = "private_residential_rentals"

# Rent per sqft per month for a set of projects, restricted to a size band and the latest
# months of leases. Bands *overlap* the window rather than sitting inside it: URA bands are only
# 100 sqft wide, so requiring the midpoint to be inside would drop genuine matches.
# `bounds` is the rental table's own latest month, which is not the sales table's.
PROJECT_YIELD_SQL = Template(
    """
    WITH bounds AS (SELECT max(lease_date) AS last_month FROM $table),
    rent AS (
        SELECT
            project,
            count() AS rentals,
            round(quantileExact(0.5)(rent)) AS median_monthly_rent,
            quantileExact(0.5)(
                rent / if(area_sqft_max IS NULL, area_sqft_min,
                          (area_sqft_min + area_sqft_max) / 2)
            ) AS rent_psf_month
        FROM $table
        WHERE project IN {projects:Array(String)}
            AND area_sqft_min > 0
            AND area_sqft_min <= {area_high:Float64}
            AND if(area_sqft_max IS NULL, area_sqft_min, area_sqft_max) >= {area_low:Float64}
            AND lease_date >= addMonths(
                (SELECT last_month FROM bounds), -({months:Int32} - 1)
            )
        GROUP BY project
    )
    SELECT * FROM rent
    """
)

COVERAGE_SQL = Template(
    """
    SELECT
        count() AS rentals,
        uniqExact(project) AS projects,
        min(lease_date) AS first_month,
        max(lease_date) AS last_month
    FROM $table
    """
)

# The rental window is anchored on the rental table, never on the sales table: URA publishes
# the two with different lags.
LATEST_MONTH_SQL = Template("SELECT max(lease_date) AS last_month FROM $table")


class PrivateRentalRepository:
    """Runs the rental queries and returns raw rows. Knows SQL, not business rules."""

    def __init__(self, accessor: Queryable) -> None:
        self._accessor = accessor

    def yield_for_projects(
        self, projects: list[str], area_low: float, area_high: float, months: int
    ) -> list[dict[str, Any]]:
        """Rent medians per project, for leased units within `months` of the latest month."""
        sql = PROJECT_YIELD_SQL.substitute(table=self._table)
        return self._accessor.query(
            sql,
            {
                "projects": list(projects),
                "area_low": area_low,
                "area_high": area_high,
                "months": months,
            },
        )

    def coverage(self) -> dict[str, Any]:
        return self._accessor.query(COVERAGE_SQL.substitute(table=self._table))[0]

    def latest_month(self) -> Any:
        """Latest month with a rental contract, so rent windows track the rental table."""
        sql = LATEST_MONTH_SQL.substitute(table=self._table)
        return self._accessor.query(sql)[0]["last_month"]

    @property
    def table_name(self) -> str:
        """The resolved rental table name, for trusted cross-table coverage queries."""
        return self._table

    @property
    def _table(self) -> str:
        return self._accessor.table(TABLE)
