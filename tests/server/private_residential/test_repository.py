from datetime import date

from sg_mcproperty.server.private_residential import (
    PrivateResidentialRepository,
    TransactionFilters,
)
from sg_mcproperty.server.private_residential.filters import PeerFilters

from .helpers import FakeAccessor, project_row, transaction_row


def test_repository_project_search_sql_and_params():
    accessor = FakeAccessor([project_row()])
    PrivateResidentialRepository(accessor).find_projects("PARKTOWN", ["PARKTOWN"], None, 10)
    sql, params = accessor.calls[0]
    assert "db.private_residential_transactions" in sql
    assert "$" not in sql and "district = " not in sql
    assert params == {"query": "PARKTOWN", "tokens": ["PARKTOWN"], "limit": 10}


def test_repository_district_filter_is_parameterised():
    accessor = FakeAccessor([])
    PrivateResidentialRepository(accessor).find_projects("X1", ["X1"], 18, 5)
    sql, params = accessor.calls[0]
    assert "district = {district:UInt8}" in sql and params["district"] == 18


def test_repository_fuzzy_uses_edit_distance():
    accessor = FakeAccessor([])
    PrivateResidentialRepository(accessor).find_projects_fuzzy("PARKTWON", ["PARKTWON"], None, 5)
    sql, _ = accessor.calls[0]
    assert "damerauLevenshteinDistance" in sql and "$" not in sql


def test_repository_transactions_count_and_list():
    accessor = FakeAccessor([{"total": 3}], [transaction_row()])
    repo = PrivateResidentialRepository(accessor)
    filters = TransactionFilters.from_inputs(project="aurea")
    assert repo.count_transactions(filters) == 3
    assert len(repo.list_transactions(filters, 5)) == 1
    count_sql, count_params = accessor.calls[0]
    list_sql, list_params = accessor.calls[1]
    assert "count()" in count_sql and "project = {project:String}" in count_sql
    assert "ORDER BY contract_date DESC" in list_sql
    assert "limit" not in count_params and list_params["limit"] == 5


# --- price trend, profile, peers, stats ---------------------------------------------------------

PEER_FILTERS = PeerFilters(property_types=("Condominium",), start_date=date(2025, 12, 1))


def test_price_trend_always_requires_a_usable_area():
    accessor = FakeAccessor([], [])
    repo = PrivateResidentialRepository(accessor)
    repo.price_trend(TransactionFilters(), "toStartOfQuarter")
    repo.price_trend(TransactionFilters.from_inputs(district=9), "toStartOfMonth")
    no_filter_sql, _ = accessor.calls[0]
    with_filter_sql, with_filter_params = accessor.calls[1]
    assert (
        "WHERE area_sqft > 0" in no_filter_sql
        and "toStartOfQuarter(contract_date)" in no_filter_sql
    )
    assert "district = {district:UInt8} AND area_sqft > 0" in with_filter_sql
    assert with_filter_params == {"district": 9}
    assert "$" not in no_filter_sql and "$" not in with_filter_sql


def test_project_profile_returns_row_or_none():
    repo = PrivateResidentialRepository(FakeAccessor([{"project": "A"}], []))
    assert repo.project_profile("A") == {"project": "A"}
    assert repo.project_profile("MISSING") is None


def test_peers_by_distance_sql_params_and_unprefixed_rows():
    accessor = FakeAccessor(
        [
            {
                "project": "P",
                "peer_street": "S",
                "peer_district": 1,
                "peer_property_type": "Condominium",
                "peer_tenure": "99 years",
                "peer_lease_year": 2000,
                "transactions": 5,
                "median_psf": 1.0,
                "median_price": 2.0,
                "distance_m": 300.0,
            }
        ]
    )
    rows = PrivateResidentialRepository(accessor).find_peers_by_distance(
        "TARGET", 1.5, 2.5, 1000, PEER_FILTERS, 3, 10
    )
    sql, params = accessor.calls[0]
    assert "$" not in sql and "x IS NOT NULL" in sql and "distance_m <= {radius_m:Float64}" in sql
    assert "property_type IN {property_types:Array(String)}" in sql
    assert params["target_x"] == 1.5 and params["radius_m"] == 1000 and params["limit"] == 10
    assert rows[0]["district"] == 1 and rows[0]["tenure"] == "99 years"  # prefix removed
    assert "peer_district" not in rows[0]


def test_peer_output_aliases_never_shadow_filtered_columns():
    # An alias equal to a column used in WHERE makes ClickHouse reject the aggregate.
    from sg_mcproperty.server.private_residential import repository

    for template in (repository.PEERS_RADIUS_SQL, repository.PEERS_AREA_SQL):
        select_part = template.template.split("FROM")[0]
        for column in ("AS district", "AS property_type", "AS tenure", "AS lease_year"):
            assert column not in select_part


def test_peers_by_area_uses_street_or_district_condition():
    accessor = FakeAccessor([], [])
    repo = PrivateResidentialRepository(accessor)
    repo.find_peers_by_area("T", "street", "BEACH ROAD", PEER_FILTERS, 3, 5)
    repo.find_peers_by_area("T", "district", 7, PEER_FILTERS, 3, 5)
    assert "street = {area_value:String}" in accessor.calls[0][0]
    assert "district = {area_value:UInt8}" in accessor.calls[1][0]
    assert accessor.calls[0][1]["area_value"] == "BEACH ROAD"


def test_target_and_pooled_stats_conditions():
    accessor = FakeAccessor([stats_dict()], [stats_dict()])
    repo = PrivateResidentialRepository(accessor)
    repo.project_stats("TARGET", PEER_FILTERS)
    repo.pooled_stats(["A", "B"], PEER_FILTERS)
    assert "project = {project:String}" in accessor.calls[0][0]
    assert "project IN {projects:Array(String)}" in accessor.calls[1][0]
    assert accessor.calls[1][1]["projects"] == ["A", "B"]
    assert "$" not in accessor.calls[0][0] + accessor.calls[1][0]


def stats_dict():
    return {
        "transactions": 1,
        "projects": 1,
        "p25_psf": 1,
        "median_psf": 1,
        "p75_psf": 1,
        "median_price": 1,
    }


# --- dataset facets and project summary ---------------------------------------------------------


def test_dataset_queries_have_no_leftover_template_fields():
    accessor = FakeAccessor(
        [{"districts": [1]}], [{"property_type": "Condominium", "transactions": 1}]
    )
    repo = PrivateResidentialRepository(accessor)
    assert repo.dataset_facets() == {"districts": [1]}
    assert repo.property_type_counts()[0]["transactions"] == 1
    assert all("$" not in sql for sql, _ in accessor.calls)
    assert "db.private_residential_transactions" in accessor.calls[0][0]


def test_project_summary_passes_dates_and_project_as_parameters():
    accessor = FakeAccessor([{"transactions": 1}])
    PrivateResidentialRepository(accessor).project_summary(
        "AUREA", date(2025, 12, 1), date(2024, 12, 1)
    )
    sql, params = accessor.calls[0]
    assert params == {"project": "AUREA", "recent_start": "2025-12-01", "prior_start": "2024-12-01"}
    assert "project = {project:String}" in sql and "$" not in sql
    assert "{recent_start:Date}" in sql and "{prior_start:Date}" in sql


def test_project_summary_user_input_stays_in_parameters():
    accessor = FakeAccessor([{"transactions": 0}])
    PrivateResidentialRepository(accessor).project_summary(
        "x'; DROP TABLE t; --", date(2025, 12, 1), date(2024, 12, 1)
    )
    sql, params = accessor.calls[0]
    assert "DROP" not in sql and "DROP" in params["project"]


# --- market activity and valuation sample -------------------------------------------------------


def test_market_activity_sql_uses_period_function_and_filters():
    accessor = FakeAccessor([])
    PrivateResidentialRepository(accessor).market_activity(
        TransactionFilters.from_inputs(district=9, segment="landed"), "toStartOfQuarter"
    )
    sql, params = accessor.calls[0]
    assert "toStartOfQuarter(contract_date)" in sql and "$" not in sql
    assert "district = {district:UInt8}" in sql and "property_type IN" in sql
    assert params["district"] == 9 and params["property_types"] == [
        "Terrace",
        "Semi-detached",
        "Detached",
    ]
    assert "countIf(type_of_sale = 'New Sale')" in sql and "LIMIT" in sql


def test_valuation_sample_is_bounded_and_parameterised():
    accessor = FakeAccessor([])
    filters = PeerFilters(
        ("Condominium",), date(2024, 12, 1), min_area_sqft=500.0, max_area_sqft=2000.0
    )
    PrivateResidentialRepository(accessor).valuation_sample(["A", "B"], filters, 500)
    sql, params = accessor.calls[0]
    assert "project IN {projects:Array(String)}" in sql and "LIMIT {limit:UInt32}" in sql
    assert "area_sqft > 0 AND price > 0" in sql and "$" not in sql
    assert params["projects"] == ["A", "B"] and params["limit"] == 500
    assert params["min_area_sqft"] == 500.0


def test_valuation_sample_user_input_stays_in_parameters():
    accessor = FakeAccessor([])
    PrivateResidentialRepository(accessor).valuation_sample(
        ["x'; DROP TABLE t; --"], PEER_FILTERS, 10
    )
    sql, params = accessor.calls[0]
    assert "DROP" not in sql and params["projects"] == ["x'; DROP TABLE t; --"]
