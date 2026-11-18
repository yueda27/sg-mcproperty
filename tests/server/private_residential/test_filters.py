from datetime import date

import pytest

from sg_mcproperty.server.private_residential import TransactionFilters
from sg_mcproperty.server.private_residential.filters import normalise_project_query


def test_normalise_project_query():
    assert normalise_project_query("  parktown   res ") == ("PARKTOWN RES", ["PARKTOWN", "RES"])


@pytest.mark.parametrize("bad", ["", " ", "a"])
def test_normalise_project_query_rejects_short(bad):
    with pytest.raises(ValueError):
        normalise_project_query(bad)


def test_no_filters_means_no_where():
    assert TransactionFilters.from_inputs().to_sql() == ("", {})


def test_all_filters_become_parameters():
    filters = TransactionFilters.from_inputs(
        project=" aurea ",
        district=7,
        type_of_sale="resale",
        start_date="2025-03-15",
        end_date="2026-01",
        min_price=1e6,
        max_price=2e6,
        min_area_sqft=500,
        max_area_sqft=900,
    )
    where, params = filters.to_sql()
    assert where.startswith("WHERE ") and where.count(" AND ") == 8
    assert params == {
        "project": "AUREA",
        "district": 7,
        "type_of_sale": "Resale",
        "start_date": "2025-03-01",
        "end_date": "2026-01-01",
        "min_price": 1e6,
        "max_price": 2e6,
        "min_area_sqft": 500,
        "max_area_sqft": 900,
    }
    assert "price >= {min_price:Float64}" in where
    assert "area_sqft <= {max_area_sqft:Float64}" in where
    assert filters.start_date == date(2025, 3, 1)


def test_user_input_never_reaches_sql_text():
    where, params = TransactionFilters.from_inputs(project="x'; DROP TABLE t; --").to_sql()
    assert "DROP" not in where
    assert "DROP" in params["project"]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"district": 0},
        {"district": 29},
        {"type_of_sale": "Lease"},
        {"start_date": "March 2025"},
        {"start_date": "2026-02", "end_date": "2025-01"},
        {"min_price": 2e6, "max_price": 1e6},
        {"min_area_sqft": 900, "max_area_sqft": 500},
    ],
)
def test_invalid_filters_rejected(kwargs):
    with pytest.raises(ValueError):
        TransactionFilters.from_inputs(**kwargs)
