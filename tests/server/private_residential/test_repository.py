from sg_mcproperty.server.private_residential import (
    PrivateResidentialRepository,
    TransactionFilters,
)

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
