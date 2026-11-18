"""Repository tests: SQL shape and parameters, never business rules."""

from datetime import datetime

from sg_mcproperty.server.private_rental import PrivateRentalRepository

from ..private_residential.helpers import FakeAccessor


def test_yield_query_targets_the_rental_table_and_parameterises_every_value():
    accessor = FakeAccessor([{"project": "A", "rentals": 3}])
    PrivateRentalRepository(accessor).yield_for_projects(["A", "B"], 1524.0, 2286.0, 12)
    sql, params = accessor.calls[0]
    assert "db.private_residential_rentals" in sql
    assert "$" not in sql
    assert params == {
        "projects": ["A", "B"],
        "area_low": 1524.0,
        "area_high": 2286.0,
        "months": 12,
    }


def test_yield_query_matches_overlapping_area_bands():
    accessor = FakeAccessor([])
    PrivateRentalRepository(accessor).yield_for_projects(["A"], 1524.0, 2286.0, 12)
    sql, _ = accessor.calls[0]
    # Bands overlap the window: the lower edge is compared on the band's top, and vice versa.
    assert "area_sqft_min <= {area_high:Float64}" in sql
    assert "if(area_sqft_max IS NULL, area_sqft_min, area_sqft_max) >= {area_low:Float64}" in sql
    assert "area_sqft_min > 0" in sql


def test_yield_query_uses_its_own_latest_month_not_the_sales_table():
    accessor = FakeAccessor([])
    PrivateRentalRepository(accessor).yield_for_projects(["A"], 1524.0, 2286.0, 12)
    sql, _ = accessor.calls[0]
    assert "max(lease_date) AS last_month" in sql
    assert "addMonths" in sql


def test_hostile_project_name_stays_in_the_parameters():
    accessor = FakeAccessor([])
    hostile = "x'; DROP TABLE t; --"
    PrivateRentalRepository(accessor).yield_for_projects([hostile], 1.0, 2.0, 12)
    sql, params = accessor.calls[0]
    assert hostile not in sql
    assert params["projects"] == [hostile]


def test_coverage_reports_the_rental_window():
    accessor = FakeAccessor(
        [
            {
                "rentals": 450_669,
                "projects": 3_225,
                "first_month": datetime(2021, 11, 1),
                "last_month": datetime(2026, 10, 1),
            }
        ]
    )
    row = PrivateRentalRepository(accessor).coverage()
    assert row["last_month"] == datetime(2026, 10, 1)
    sql, params = accessor.calls[0]
    assert "uniqExact(project)" in sql and params is None
