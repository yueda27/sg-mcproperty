from datetime import date

import pytest

from sg_mcproperty.server.private_residential import (
    AffordabilityQuery,
    GrowthQuery,
    NearbyQuery,
    TransactionFilters,
    ValuationQuery,
)
from sg_mcproperty.server.private_residential.filters import (
    MAX_AFFORDABLE_RESULTS,
    PeerFilters,
    add_months,
    normalise_project_name,
    normalise_project_query,
    period_start,
    resolve_segment,
    segment_of,
    validate_period,
)


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


# --- segments, periods, dates -------------------------------------------------------------------


def test_resolve_segment():
    assert resolve_segment(None) is None
    assert resolve_segment("ALL") is None
    assert resolve_segment(" Non-Landed ") == "non-landed"
    assert resolve_segment("auto") == "non-landed"  # market-wide questions
    assert resolve_segment("auto", project="AUREA") is None  # a named project decides itself
    with pytest.raises(ValueError):
        resolve_segment("houses")


def test_segment_of_groups_comparable_types_and_isolates_unknown():
    assert segment_of("Apartment") == segment_of("Condominium")
    assert segment_of("Condominium")[0] == "non-landed"
    assert segment_of("Strata Terrace")[0] == "strata-landed"
    assert segment_of("Terrace")[0] == "landed"
    assert segment_of("Houseboat") == ("Houseboat", ("Houseboat",))


def test_segment_filter_becomes_a_parameter():
    filters = TransactionFilters.from_inputs(segment="landed")
    where, params = filters.to_sql()
    assert "property_type IN {property_types:Array(String)}" in where
    assert params["property_types"] == ["Terrace", "Semi-detached", "Detached"]
    assert filters.segment == "landed"


def test_price_trend_default_segment_depends_on_project():
    assert TransactionFilters.from_inputs(segment="auto").segment == "non-landed"
    assert TransactionFilters.from_inputs(project="aurea", segment="auto").segment is None


def test_validate_period():
    assert validate_period(" Quarter ") == "quarter"
    with pytest.raises(ValueError):
        validate_period("week")


def test_add_months_crosses_years_both_ways():
    assert add_months(date(2026, 11, 1), -11) == date(2025, 12, 1)
    assert add_months(date(2026, 1, 1), -1) == date(2025, 12, 1)
    assert add_months(date(2025, 12, 1), 2) == date(2026, 2, 1)


# --- peer filters and nearby query --------------------------------------------------------------


def test_peer_filters_only_emit_what_is_set():
    base = PeerFilters(property_types=("Condominium",), start_date=date(2025, 12, 1))
    sql, params = base.to_sql()
    assert sql.count("AND") == 2 and "tenure" not in sql
    assert params == {"property_types": ["Condominium"], "start_date": "2025-12-01"}

    full = PeerFilters(
        ("Condominium",), date(2025, 12, 1), "Resale", 800.0, 1200.0, True, 2010, 2020
    )
    sql, params = full.to_sql()
    assert "(tenure = 'Freehold') = {freehold:UInt8}" in sql
    assert params["freehold"] == 1 and params["min_lease_year"] == 2010
    assert "$" not in sql


def test_nearby_query_normalises_and_computes_size_band():
    query = NearbyQuery.from_inputs(
        " treasure  at tampines ", type_of_sale="resale", area_sqft=1000
    )
    assert query.project == "TREASURE AT TAMPINES" and query.type_of_sale == "Resale"
    assert query.size_band() == (800.0, 1200.0)
    assert NearbyQuery.from_inputs("x1").size_band() == (None, None)
    assert NearbyQuery.from_inputs("x1", max_peers=50).max_peers == 50


@pytest.mark.parametrize(
    "kwargs",
    [
        {"project": " "},
        {"radius_m": 50},
        {"radius_m": 5000},
        {"months": 0},
        {"months": 61},
        {"max_peers": 0},
        {"max_peers": 51},
        {"area_sqft": 0},
        {"type_of_sale": "Lease"},
        {"lease_year_tolerance": -1},
        {"lease_year_tolerance": 61},
    ],
)
def test_nearby_query_rejects_bad_inputs(kwargs):
    with pytest.raises(ValueError):
        NearbyQuery.from_inputs(**{"project": "AUREA", **kwargs})


def test_normalise_project_name():
    assert normalise_project_name("  parktown   residence ") == "PARKTOWN RESIDENCE"
    with pytest.raises(ValueError):
        normalise_project_name("   ")


def test_period_start():
    day = date(2026, 11, 17)
    assert period_start(day, "month") == date(2026, 11, 1)
    assert period_start(day, "quarter") == date(2026, 10, 1)
    assert period_start(date(2026, 3, 31), "quarter") == date(2026, 1, 1)
    assert period_start(day, "year") == date(2026, 1, 1)


def test_valuation_query_normalises_and_defaults():
    query = ValuationQuery.from_inputs(
        " treasure  at tampines ", 1000, floor=12, type_of_sale="new sale"
    )
    assert query.project == "TREASURE AT TAMPINES" and query.type_of_sale == "New Sale"
    assert (query.radius_m, query.months, query.max_peers) == (1500, 24, 12)
    assert ValuationQuery.from_inputs("x1", 900).type_of_sale is None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"project": " "},
        {"area_sqft": 0},
        {"area_sqft": -5},
        {"area_sqft": 50_000},
        {"floor": 0},
        {"floor": 101},
        {"radius_m": 99},
        {"radius_m": 3001},
        {"months": 5},
        {"months": 61},
        {"max_peers": 0},
        {"max_peers": 51},
        {"type_of_sale": "Lease"},
    ],
)
def test_valuation_query_rejects_bad_inputs(kwargs):
    with pytest.raises(ValueError):
        ValuationQuery.from_inputs(**{"project": "AUREA", "area_sqft": 900, **kwargs})


# --- growth scan and affordability ---------------------------------------------------------------


def test_growth_query_normalises_and_defaults():
    query = GrowthQuery.from_inputs(
        " treasure  at tampines ",
        min_area_sqft=1200,
        max_area_sqft=2000,
        tenure="Freehold or 999 years",
    )
    assert query.project == "TREASURE AT TAMPINES"
    assert (query.radius_m, query.months, query.max_peers, query.min_transactions) == (
        2000,
        60,
        50,
        4,
    )
    assert (query.min_area_sqft, query.max_area_sqft) == (1200, 2000)
    assert (query.segment, query.tenure) == ("non-landed", "freehold or 999 years")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"project": " "},
        {"radius_m": 50},
        {"radius_m": 3001},
        {"months": 0},
        {"months": 61},
        {"max_peers": 0},
        {"max_peers": 51},
        {"min_transactions": 0},
        {"min_transactions": 51},
        {"min_area_sqft": 0},
        {"max_area_sqft": -1},
        {"min_area_sqft": 2000, "max_area_sqft": 1200},
        {"segment": "hotel"},
        {"tenure": "leasehold"},
    ],
)
def test_growth_query_rejects_bad_inputs(kwargs):
    with pytest.raises(ValueError):
        GrowthQuery.from_inputs(**{"project": "AUREA", **kwargs})


def test_affordability_query_clamps_and_validates():
    query = AffordabilityQuery.from_inputs(1_500_000, district=15, limit=500)
    assert (query.max_price, query.district, query.segment, query.limit) == (
        1_500_000,
        15,
        "non-landed",
        MAX_AFFORDABLE_RESULTS,
    )

    for kwargs in (
        {"max_price": 0},
        {"max_price": -1},
        {"max_price": 100_000_001},
        {"min_area_sqft": 0},
        {"min_transactions": 0},
        {"segment": "hotel"},
        {"district": 29},
    ):
        with pytest.raises(ValueError):
            AffordabilityQuery.from_inputs(**{"max_price": 1_000_000, **kwargs})
