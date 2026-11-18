from datetime import date

import pytest

from sg_mcproperty.accessor import ClickHouseAccessor
from sg_mcproperty.server.private_residential import (
    PrivateResidentialRepository,
    PrivateResidentialService,
    TransactionFilters,
)
from sg_mcproperty.server.private_residential.service import (
    MAX_PROJECT_RESULTS,
    MAX_TRANSACTION_RESULTS,
)

from .helpers import FakeRepository, project_row, transaction_row


def test_service_search_projects_cleans_rows():
    service = PrivateResidentialService(FakeRepository(projects=[project_row()]))
    out = service.search_projects("parktown")
    assert out[0]["first_sale"] == "2023-01-01" and out[0]["last_sale"] == "2026-04-01"


def test_service_falls_back_to_fuzzy_and_hides_ranking_column():
    repo = FakeRepository(fuzzy=[project_row(match_type="fuzzy", edits=1)])
    out = PrivateResidentialService(repo).search_projects("parktwon")
    assert [call[0] for call in repo.calls] == ["find_projects", "find_projects_fuzzy"]
    assert out[0]["match_type"] == "fuzzy" and "edits" not in out[0]


def test_service_skips_fuzzy_when_exact_matches_exist():
    repo = FakeRepository(projects=[project_row()])
    PrivateResidentialService(repo).search_projects("parktown")
    assert [call[0] for call in repo.calls] == ["find_projects"]


def test_service_caps_limits_and_validates_district():
    repo = FakeRepository(projects=[project_row()])
    service = PrivateResidentialService(repo)
    service.search_projects("parktown", limit=1000)
    assert repo.calls[0][4] == MAX_PROJECT_RESULTS
    with pytest.raises(ValueError):
        service.search_projects("parktown", district=99)

    repo = FakeRepository(transactions=[transaction_row()], total=1)
    PrivateResidentialService(repo).search_transactions(TransactionFilters(), limit=10_000)
    assert repo.calls[0][2] == MAX_TRANSACTION_RESULTS


def test_service_transaction_page():
    repo = FakeRepository(transactions=[transaction_row(), transaction_row()], total=57)
    page = PrivateResidentialService(repo).search_transactions(TransactionFilters(), limit=2)
    assert page["total_matches"] == 57 and page["returned"] == 2
    assert page["transactions"][0]["contract_date"] == "2026-11-01"


def test_service_coverage_handles_empty_table():
    empty = {"transactions": 0, "projects": 0, "first_month": date(1970, 1, 1), "last_month": None}
    out = PrivateResidentialService(FakeRepository(coverage=empty)).coverage()
    assert out == {"transactions": 0, "projects": 0, "first_month": None, "last_month": None}


# --- live ClickHouse (skipped when unavailable) -------------------------------------------------


@pytest.fixture
def live_service():
    accessor = ClickHouseAccessor()
    if not accessor.ping():
        pytest.skip("ClickHouse not reachable")
    service = PrivateResidentialService(PrivateResidentialRepository(accessor))
    if service.coverage()["transactions"] == 0:
        pytest.skip("private_residential_transactions is empty")
    return service


def test_live_project_search_and_typo(live_service):
    assert live_service.search_projects("parktown res")[0]["project"] == "PARKTOWN RESIDENCE"
    typo = live_service.search_projects("parktwon")[0]
    assert typo["project"] == "PARKTOWN RESIDENCE" and typo["match_type"] == "fuzzy"


def test_live_transaction_filters(live_service):
    filters = TransactionFilters.from_inputs(
        project="aurea", type_of_sale="new sale", start_date="2026-01"
    )
    page = live_service.search_transactions(filters, limit=5)
    assert 0 < page["returned"] <= 5
    dates = [t["contract_date"] for t in page["transactions"]]
    assert dates == sorted(dates, reverse=True) and min(dates) >= "2026-01-01"
    assert all(t["project"] == "AUREA" for t in page["transactions"])


def test_live_coverage(live_service):
    coverage = live_service.coverage()
    assert coverage["transactions"] > 0 and coverage["first_month"] <= coverage["last_month"]
