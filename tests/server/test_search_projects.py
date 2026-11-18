import pytest

from sg_mcproperty.accessor import ClickHouseAccessor
from sg_mcproperty.server.tools.private_residential import (
    MAX_PROJECT_RESULTS,
    normalise_query,
    search_projects,
)


class FakeAccessor:
    """Records queries and returns canned rows, one list per call."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def table(self, name):
        return f"db.{name}"

    def query(self, sql, parameters=None):
        self.calls.append((sql, parameters))
        return self.responses.pop(0) if self.responses else []


def _row(**overrides):
    row = {
        "project": "PARKTOWN RESIDENCE",
        "street": "TAMPINES STREET 62",
        "district": 18,
        "tenure": "99 years",
        "lease_year": 2023,
        "transactions": 10,
        "first_sale": __import__("datetime").datetime(2023, 1, 1),
        "last_sale": __import__("datetime").datetime(2026, 4, 1),
        "match_type": "prefix",
    }
    return {**row, **overrides}


def test_normalise_query():
    assert normalise_query("  parktown   res ") == ("PARKTOWN RES", ["PARKTOWN", "RES"])


@pytest.mark.parametrize("bad", ["", " ", "a"])
def test_normalise_query_rejects_short(bad):
    with pytest.raises(ValueError):
        normalise_query(bad)


def test_search_uses_parameters_and_cleans_rows():
    fake = FakeAccessor([_row()])
    out = search_projects(fake, "parktown")
    sql, params = fake.calls[0]
    assert "db.private_residential_transactions" in sql
    assert "$" not in sql  # all template fields substituted
    assert "district = " not in sql
    assert params == {"query": "PARKTOWN", "tokens": ["PARKTOWN"], "limit": 10}
    assert out[0]["first_sale"] == "2023-01-01"
    assert out[0]["last_sale"] == "2026-04-01"
    assert len(fake.calls) == 1  # no fuzzy fallback when there are matches


def test_search_never_interpolates_user_input():
    fake = FakeAccessor([_row()])
    search_projects(fake, "x'; DROP TABLE t; --")
    sql, params = fake.calls[0]
    assert "DROP" not in sql
    assert "DROP" in params["query"]


def test_district_filter_and_limit_cap():
    fake = FakeAccessor([_row()])
    search_projects(fake, "parktown", district=18, limit=1000)
    sql, params = fake.calls[0]
    assert "district = {district:UInt8}" in sql
    assert params["district"] == 18
    assert params["limit"] == MAX_PROJECT_RESULTS


def test_fuzzy_fallback_when_no_match():
    fake = FakeAccessor([], [_row(match_type="fuzzy", edits=1)])
    out = search_projects(fake, "parktwon")
    assert len(fake.calls) == 2
    assert "damerauLevenshteinDistance" in fake.calls[1][0]
    assert "$" not in fake.calls[1][0]
    assert out[0]["match_type"] == "fuzzy"
    assert "edits" not in out[0]


@pytest.fixture
def live():
    accessor = ClickHouseAccessor()
    if not accessor.ping():
        pytest.skip("ClickHouse not reachable")
    return accessor


def test_live_search(live):
    rows = search_projects(live, "parktown res")
    if not rows:
        pytest.skip("private_residential_transactions is empty")
    assert rows[0]["project"].startswith("PARKTOWN")
    assert rows[0]["transactions"] > 0


def test_live_typo_search(live):
    rows = search_projects(live, "parktwon")
    if not rows:
        pytest.skip("private_residential_transactions is empty")
    assert rows[0]["project"] == "PARKTOWN RESIDENCE"
    assert rows[0]["match_type"] == "fuzzy"
