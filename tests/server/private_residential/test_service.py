import math
from datetime import date

import pytest

from sg_mcproperty.accessor import ClickHouseAccessor
from sg_mcproperty.server.private_residential import (
    NearbyQuery,
    PrivateResidentialRepository,
    PrivateResidentialService,
    TransactionFilters,
    ValuationQuery,
)
from sg_mcproperty.server.private_residential.service import (
    MAX_PROJECT_RESULTS,
    MAX_TRANSACTION_RESULTS,
)

from .helpers import (
    FakeAnalysisRepository,
    FakeRepository,
    activity_row,
    facets_row,
    peer_row,
    profile_row,
    project_row,
    stats_row,
    summary_row,
    synthetic_sample,
    transaction_row,
)


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


# --- price trend --------------------------------------------------------------------------------


def _trend_row(period, transactions, psf):
    return {
        "period": period,
        "transactions": transactions,
        "median_price": psf * 1000.0,
        "median_psf": float(psf),
    }


def test_price_trend_points_flags_and_change():
    rows = [
        _trend_row(date(2025, 1, 1), 20, 2000),
        _trend_row(date(2025, 4, 1), 2, 3500),  # thin sample: ignored for the change
        _trend_row(date(2025, 7, 1), 12, 2200),
    ]
    repo = FakeAnalysisRepository(trend=rows)
    out = PrivateResidentialService(repo).price_trend(
        TransactionFilters.from_inputs(project="aurea", segment="auto"), "Quarter"
    )
    assert repo.calls[0][2] == "toStartOfQuarter"
    assert [p["period"] for p in out["points"]] == ["2025-01-01", "2025-04-01", "2025-07-01"]
    assert [p["low_sample"] for p in out["points"]] == [False, True, False]
    assert out["psf_change_pct"] == 10.0 and out["period"] == "quarter"
    assert any("low_sample" in note for note in out["notes"])


def test_price_trend_empty_and_single_period_have_no_change():
    empty = PrivateResidentialService(FakeAnalysisRepository()).price_trend(TransactionFilters())
    assert empty["points"] == [] and empty["psf_change_pct"] is None
    assert "No transactions" in empty["notes"][0]

    one = FakeAnalysisRepository(trend=[_trend_row(date(2025, 1, 1), 20, 2000)])
    assert (
        PrivateResidentialService(one).price_trend(TransactionFilters())["psf_change_pct"] is None
    )


def test_price_trend_warns_when_segments_are_mixed():
    out = PrivateResidentialService(FakeAnalysisRepository()).price_trend(TransactionFilters())
    assert any("landed and non-landed" in note for note in out["notes"])


def test_price_trend_rejects_bad_period():
    with pytest.raises(ValueError):
        PrivateResidentialService(FakeAnalysisRepository()).price_trend(
            TransactionFilters(), "week"
        )


# --- compare nearby -----------------------------------------------------------------------------


def _nearby(repo, **kwargs):
    return PrivateResidentialService(repo).compare_nearby(
        NearbyQuery.from_inputs("target", **kwargs)
    )


def test_nearby_unknown_project_points_to_search_projects():
    with pytest.raises(ValueError, match="search_projects"):
        _nearby(FakeAnalysisRepository(profile=None))


def test_nearby_by_distance_premium_and_indicative_value():
    repo = FakeAnalysisRepository(
        profile=profile_row(),
        distance_peers=[peer_row("A", 400), peer_row("B", 900)],
        target=stats_row(transactions=30, median=2400.0),
        pooled=stats_row(transactions=60, projects=2, p25=1800.0, median=2000.0, p75=2200.0),
    )
    out = _nearby(repo, area_sqft=1000, radius_m=1500)
    assert out["basis"] == "radius" and out["radius_m"] == 1500 and out["segment"] == "non-landed"
    assert out["window_start"] == "2025-12-01" and out["window_end"] == "2026-11-01"
    assert out["premium_pct"] == 20.0  # 2400 vs 2000
    assert out["indicative_value"] == {
        "area_sqft": 1000, "low": 1_800_000, "mid": 2_000_000, "high": 2_200_000
    }  # fmt: skip
    assert [p["project"] for p in out["peers"]] == ["A", "B"]
    assert out["peer_group"]["projects"] == 2 and out["target"]["stats"]["median_psf"] == 2400.0
    # peers and target share the +/-20% size band, the pooled stats cover exactly the peers
    distance_filters = repo.calls[1][4]
    assert (distance_filters.min_area_sqft, distance_filters.max_area_sqft) == (800.0, 1200.0)
    assert ("pooled_stats", ["A", "B"], distance_filters) in repo.calls


def test_nearby_falls_back_street_then_district_when_no_coordinates():
    no_xy = profile_row(x=None, y=None)
    street = FakeAnalysisRepository(profile=no_xy, street_peers=[peer_row("S")])
    out = _nearby(street)
    assert out["basis"] == "street" and out["radius_m"] is None
    assert any("no coordinates" in n for n in out["notes"])

    district = FakeAnalysisRepository(profile=no_xy, district_peers=[peer_row("D")])
    out = _nearby(district)
    assert out["basis"] == "district"
    assert [c[0] for c in district.calls if c[0] in ("street", "district")] == [
        "street",
        "district",
    ]


def test_nearby_without_peers_explains_and_has_no_premium():
    out = _nearby(FakeAnalysisRepository(profile=profile_row()))
    assert out["peers"] == [] and out["peer_group"] is None and out["premium_pct"] is None
    assert any("No peer" in n for n in out["notes"])


def test_nearby_handles_nan_stats_when_target_has_no_sales():
    repo = FakeAnalysisRepository(
        profile=profile_row(),
        distance_peers=[peer_row()],
        target={**stats_row(transactions=0), "median_psf": float("nan")},
    )
    out = _nearby(repo)
    assert out["target"]["stats"] is None and out["premium_pct"] is None
    assert any("no matching transactions" in n for n in out["notes"])


def test_nearby_notes_flag_thin_sample_tenure_and_age():
    repo = FakeAnalysisRepository(
        profile=profile_row(tenure="99 years", lease_year=2018),
        distance_peers=[
            peer_row("OLD", lease_year=1995),
            peer_row("FH", tenure="Freehold", lease_year=None),
        ],
        target=stats_row(transactions=2),
        pooled=stats_row(transactions=30, projects=2),
    )
    notes = " | ".join(_nearby(repo)["notes"])
    assert "unreliable" in notes and "different tenure type" in notes
    assert "lease start more than 15 years" in notes and "Only 2 peer projects" in notes


def test_nearby_lease_year_tolerance_filters_peers_but_not_the_target():
    repo = FakeAnalysisRepository(profile=profile_row(lease_year=2018), distance_peers=[peer_row()])
    _nearby(repo, lease_year_tolerance=8)
    peer_filters = repo.calls[1][4]
    assert (peer_filters.min_lease_year, peer_filters.max_lease_year) == (2010, 2026)
    target_filters = next(c for c in repo.calls if c[0] == "project_stats")[2]
    assert target_filters.min_lease_year is None and target_filters.freehold is None


def test_nearby_lease_year_tolerance_ignored_for_freehold():
    repo = FakeAnalysisRepository(
        profile=profile_row(tenure="Freehold", lease_year=None), distance_peers=[peer_row()]
    )
    out = _nearby(repo, lease_year_tolerance=10)
    assert repo.calls[1][4].min_lease_year is None
    assert any("freehold" in n and "ignored" in n for n in out["notes"])


def test_nearby_same_tenure_type_filters_peers():
    repo = FakeAnalysisRepository(
        profile=profile_row(tenure="Freehold", lease_year=None), distance_peers=[peer_row()]
    )
    _nearby(repo, same_tenure_type=True)
    assert repo.calls[1][4].freehold is True


# --- dataset info -------------------------------------------------------------------------------


def _dataset_info(**kwargs):
    return PrivateResidentialService(FakeAnalysisRepository(**kwargs)).dataset_info()


def test_dataset_info_groups_property_types_into_segments_in_order():
    out = _dataset_info(
        type_counts=[
            {"property_type": "Condominium", "transactions": 60},
            {"property_type": "Terrace", "transactions": 10},
            {"property_type": "Apartment", "transactions": 30},
            {"property_type": "Executive Condominium", "transactions": 5},
        ]
    )
    assert [s["segment"] for s in out["segments"]] == ["non-landed", "ec", "landed"]
    non_landed = out["segments"][0]
    assert non_landed["property_types"] == ["Apartment", "Condominium"]
    assert non_landed["transactions"] == 90
    assert out["districts"] == [1, 2, 3] and out["types_of_sale"] == [
        "New Sale",
        "Resale",
        "Sub Sale",
    ]
    assert out["first_month"] == "2021-11-01" and out["last_month"] == "2026-11-01"


def test_dataset_info_keeps_unknown_types_separate_and_reports_coverage():
    out = _dataset_info(
        type_counts=[
            {"property_type": "Houseboat", "transactions": 2},
            {"property_type": "", "transactions": 3},
        ]
    )
    assert {s["segment"]: s["transactions"] for s in out["segments"]} == {
        "Houseboat": 2,
        "unknown": 3,
    }
    assert out["coordinate_coverage"] == {"projects_pct": 95.0, "transactions_pct": 80.0}
    notes = " | ".join(out["notes"])
    assert "20.0% of transactions have no project coordinates" in notes
    assert "no property type" in notes


def test_dataset_info_has_no_coverage_note_when_complete_and_handles_nan():
    complete = _dataset_info(facets=facets_row(transactions_with_coordinates_pct=100.0))
    assert not any("coordinates" in n for n in complete["notes"])
    empty = _dataset_info(
        facets=facets_row(
            projects_with_coordinates_pct=float("nan"),
            transactions_with_coordinates_pct=float("nan"),
        )
    )
    assert empty["coordinate_coverage"] == {"projects_pct": None, "transactions_pct": None}


# --- project summary ----------------------------------------------------------------------------


def _summary(profile=None, **summary):
    repo = FakeAnalysisRepository(profile=profile or profile_row(), summary=summary_row(**summary))
    return PrivateResidentialService(repo).project_summary(" target "), repo


def test_project_summary_shapes_everything():
    out, repo = _summary(profile_row(tenure="99 years", lease_year=2018))
    assert out["project"] == "TARGET" and out["segment"] == "non-landed"
    assert out["has_coordinates"] is True and out["lease_year"] == 2018
    assert out["first_sale"] == "2025-03-01" and out["last_sale"] == "2026-11-01"
    assert out["sales_mix"] == {"new_sale": 70, "sub_sale": 1, "resale": 4}
    assert out["price"] == {"min": 1_700_000.0, "median": 2_000_000.0, "max": 12_000_000.0}
    assert out["psf"] == {"p25": 2700.0, "median": 2900.0, "p75": 3000.0}
    assert out["area_sqft"]["median"] == 700.0
    assert out["notes"] == []
    # the two 12 month windows end at the latest data month and sit side by side
    _, name, recent_start, prior_start = next(c for c in repo.calls if c[0] == "project_summary")
    assert (name, recent_start, prior_start) == ("TARGET", date(2025, 12, 1), date(2024, 12, 1))
    last_12 = out["last_12_months"]
    assert last_12["recent_start"] == "2025-12-01" and last_12["prior_start"] == "2024-12-01"
    assert last_12["change_pct"] == 3.4  # 3000 vs 2900


def test_project_summary_change_needs_enough_sales_in_both_windows():
    thin_recent, _ = _summary(recent_transactions=2)
    assert thin_recent["last_12_months"]["change_pct"] is None
    assert any("Only 2 sales" in n for n in thin_recent["notes"])

    thin_prior, _ = _summary(prior_transactions=1)
    assert thin_prior["last_12_months"]["change_pct"] is None
    assert any("prior 12 months" in n for n in thin_prior["notes"])


def test_project_summary_no_recent_sales_and_nan_psf():
    out, _ = _summary(recent_transactions=0, recent_median_psf=float("nan"), prior_transactions=10)
    assert out["last_12_months"]["recent_median_psf"] is None
    assert out["last_12_months"]["change_pct"] is None
    assert any("No sales in the latest 12 months" in n for n in out["notes"])


def test_project_summary_flags_landed_and_missing_coordinates():
    out, _ = _summary(profile_row(property_type="Terrace", x=None, y=None))
    assert out["segment"] == "landed" and out["has_coordinates"] is False
    assert any("Landed psf" in n for n in out["notes"])


def test_project_summary_unknown_or_empty_project():
    with pytest.raises(ValueError, match="search_projects"):
        PrivateResidentialService(FakeAnalysisRepository(profile=None)).project_summary("nope")
    with pytest.raises(ValueError):
        PrivateResidentialService(FakeAnalysisRepository()).project_summary("  ")


# --- market activity ---------------------------------------------------------------------------


def _activity(rows, period="month", **filters):
    repo = FakeAnalysisRepository(activity=rows)
    out = PrivateResidentialService(repo).market_activity(
        TransactionFilters.from_inputs(segment="auto", **filters), period
    )
    return out, repo


def test_market_activity_fills_quiet_periods_through_the_latest_data_month():
    rows = [activity_row(date(2026, 5, 1), 10), activity_row(date(2026, 8, 1), 4, new=4)]
    out, repo = _activity(rows)
    assert [p["period"] for p in out["points"]] == [
        "2026-05-01", "2026-06-01", "2026-07-01", "2026-08-01",
        "2026-09-01", "2026-10-01", "2026-11-01",
    ]  # fmt: skip
    quiet = out["points"][1]
    assert quiet["transactions"] == 0 and quiet["median_psf"] is None
    assert repo.calls[0][2] == "toStartOfMonth"
    assert out["total_transactions"] == 14 and out["average_per_period"] == 2.0


def test_market_activity_compares_complete_periods_and_skips_the_latest():
    rows = [
        activity_row(date(2026, m, 1), n)
        for m, n in ((5, 6), (6, 6), (7, 6), (8, 12), (9, 12), (10, 12), (11, 1))
    ]  # Nov: partial
    out, _ = _activity(rows)
    assert out["recent_periods"] == 3
    assert (
        out["recent_transactions"] == 36 and out["prior_transactions"] == 18
    )  # Aug-Oct vs May-Jul
    assert out["volume_change_pct"] == 100.0
    assert any("may be incomplete" in n for n in out["notes"])


def test_market_activity_quarters_and_sale_type_split():
    rows = [activity_row(date(2026, 1, 1), 9, new=3, sub=2), activity_row(date(2026, 4, 1), 5)]
    out, repo = _activity(rows, period="quarter")
    assert repo.calls[0][2] == "toStartOfQuarter" and out["recent_periods"] == 2
    first = out["points"][0]
    assert (first["new_sales"], first["sub_sales"], first["resales"]) == (3, 2, 4)
    assert [p["period"] for p in out["points"]][-1] == "2026-10-01"


def test_market_activity_empty_and_no_prior_sales():
    empty, _ = _activity([])
    assert empty["points"] == [] and empty["average_per_period"] is None
    assert "No transactions" in empty["notes"][0] and empty["volume_change_pct"] is None
    only_recent, _ = _activity([activity_row(date(2026, 10, 1), 5)])
    assert only_recent["prior_transactions"] == 0 and only_recent["volume_change_pct"] is None


def test_market_activity_notes_mixed_segments_and_rejects_bad_period():
    mixed = PrivateResidentialService(
        FakeAnalysisRepository(activity=[activity_row(date(2026, 1, 1))])
    )
    out = mixed.market_activity(TransactionFilters.from_inputs(segment="all"), "month")
    assert any("landed and non-landed" in n for n in out["notes"])
    with pytest.raises(ValueError):
        mixed.market_activity(TransactionFilters(), "week")


# --- estimate value -----------------------------------------------------------------------------

LEVELS = {"TARGET": 13.0, "NEAR A": 13.3, "NEAR B": 12.8}


def _estimate(profile=None, sample=None, query=None, **repo_kwargs):
    repo = FakeAnalysisRepository(
        profile=profile or profile_row(),
        distance_peers=[peer_row("NEAR A", 400), peer_row("NEAR B", 900)],
        sample=synthetic_sample(LEVELS) if sample is None else sample,
        **repo_kwargs,
    )
    query = query or ValuationQuery.from_inputs("target", 1000, floor=12)
    return PrivateResidentialService(repo).estimate_value(query), repo


def test_estimate_value_project_mode_matches_the_known_truth():
    out, repo = _estimate()
    truth = math.exp(13.0 + 0.9 * math.log(1000) + 0.03 * 1.2 + 0.004 * 23)
    assert out["model"]["mode"] == "project" and out["model"]["r_squared"] > 0.95
    assert out["estimate"]["median"] == pytest.approx(truth, rel=0.02)
    assert out["estimate"]["low"] < out["estimate"]["median"] < out["estimate"]["high"]
    assert out["estimate"]["psf"] == pytest.approx(out["estimate"]["median"] / 1000, abs=1)
    assert out["unit"] == {
        "area_sqft": 1000.0, "floor": 12, "type_of_sale": "Resale",
        "remaining_lease_years": 91.0, "freehold": False,
    }  # fmt: skip
    # the sample is the target plus its peers, limited to sizes half to double the unit
    _, names, filters, limit = next(c for c in repo.calls if c[0] == "valuation_sample")
    assert names == ["TARGET", "NEAR A", "NEAR B"] and limit == 20_000
    assert (filters.min_area_sqft, filters.max_area_sqft) == (500.0, 2000.0)
    assert (out["window_start"], out["window_end"]) == ("2024-12-01", "2026-11-01")


def test_estimate_value_effects_are_reported_as_percentages():
    (effects,) = [_estimate()[0]["effects"]]
    assert effects["size_plus_10pct_area_pct"] == pytest.approx((1.1**0.9 - 1) * 100, abs=0.2)
    # the floor effect is small next to the 3% noise, so allow about two standard errors
    assert effects["per_10_floors_pct"] == pytest.approx((math.exp(0.03) - 1) * 100, abs=1.0)
    assert effects["new_sale_premium_pct"] == pytest.approx((math.exp(0.07) - 1) * 100, abs=0.5)
    assert effects["monthly_trend_pct"] == pytest.approx((math.exp(0.004) - 1) * 100, abs=0.25)
    # TARGET (13.0) is between the dearer NEAR A (13.3) and cheaper NEAR B (12.8): about -5% to +5%
    assert effects["target_project_premium_pct"] == pytest.approx(
        -((13.3 - 13.0) + (12.8 - 13.0)) / 2 * 100, abs=2
    )
    assert effects["per_10_years_lease_pct"] is None and effects["freehold_premium_pct"] is None


def test_estimate_value_new_sale_prices_above_resale():
    resale, _ = _estimate()
    new, _ = _estimate(
        query=ValuationQuery.from_inputs("target", 1000, floor=12, type_of_sale="new sale")
    )
    assert new["unit"]["type_of_sale"] == "New Sale"
    ratio = new["estimate"]["median"] / resale["estimate"]["median"]
    assert ratio == pytest.approx(math.exp(0.07), abs=0.01)
    assert any("Valued as a resale unit" in n for n in resale["notes"])
    assert not any("Valued as a resale unit" in n for n in new["notes"])


def test_estimate_value_comparable_check_prefers_the_projects_own_sales():
    out, _ = _estimate()
    check = out["comparable_check"]
    assert check["source"] == "project" and check["sales_used"] >= 5
    assert check["implied_value"] == pytest.approx(check["median_psf"] * 1000, abs=1000)


def test_estimate_value_area_mode_when_the_project_has_few_sales():
    sample = [r for r in synthetic_sample(LEVELS) if r["project"] != "TARGET"] + [
        r for r in synthetic_sample({"TARGET": 13.0}, per_project=2)
    ]
    out, _ = _estimate(sample=sample)
    assert out["model"]["mode"] == "area"
    assert any("only 2 sales" in n and "confounded" in n for n in out["notes"])
    none, _ = _estimate(sample=[r for r in synthetic_sample(LEVELS) if r["project"] != "TARGET"])
    assert any("no sales in the window" in n for n in none["notes"])
    assert out["comparable_check"]["source"] == "nearby"


def test_estimate_value_without_enough_sales_returns_no_estimate():
    out, _ = _estimate(sample=synthetic_sample(LEVELS, per_project=5))
    assert out["estimate"] is None and out["model"] is None and out["effects"] is None
    assert any("no estimate was made" in n for n in out["notes"])


def test_estimate_value_default_floor_is_the_floor_typically_sold():
    out, _ = _estimate(query=ValuationQuery.from_inputs("target", 1000))
    assert 1 <= out["unit"]["floor"] <= 30


def test_estimate_value_warns_about_extrapolation_noise_and_small_samples():
    big_unit, _ = _estimate(query=ValuationQuery.from_inputs("target", 1400, floor=12))
    assert not any("extrapolates" in n for n in big_unit["notes"])  # inside the sizes sold
    outside, _ = _estimate(query=ValuationQuery.from_inputs("target", 2000, floor=60))
    notes = " | ".join(outside["notes"])
    assert "size is outside" in notes and "floor is outside" in notes
    noisy, _ = _estimate(sample=synthetic_sample(LEVELS, noise=0.25))
    assert any("rough" in n or "Weak fit" in n for n in noisy["notes"])
    small, _ = _estimate(sample=synthetic_sample(LEVELS, per_project=12))
    assert any("Small sample" in n for n in small["notes"])


def test_estimate_value_falls_back_to_street_when_the_project_has_no_coordinates():
    repo = FakeAnalysisRepository(
        profile=profile_row(x=None, y=None),
        street_peers=[peer_row("NEAR A"), peer_row("NEAR B")],
        sample=synthetic_sample(LEVELS),
    )
    out = PrivateResidentialService(repo).estimate_value(ValuationQuery.from_inputs("target", 1000))
    assert out["basis"] == "street" and out["radius_m"] is None
    assert any("no coordinates" in n for n in out["notes"])


def test_estimate_value_landed_uses_no_floor():
    sample = [{**r, "floor": None} for r in synthetic_sample(LEVELS)]
    out, _ = _estimate(profile=profile_row(property_type="Terrace"), sample=sample)
    assert out["segment"] == "landed" and out["unit"]["floor"] is None
    assert "floor_10" not in out["model"]["features"]


def test_estimate_value_freehold_unit_has_no_remaining_lease():
    out, _ = _estimate(profile=profile_row(tenure="Freehold", lease_year=None))
    assert out["unit"]["freehold"] is True and out["unit"]["remaining_lease_years"] is None


def test_estimate_value_activity_uses_complete_months_only():
    out, _ = _estimate()
    activity = out["activity"]
    assert activity["sales_in_sample"] >= out["model"]["sales_used"]
    assert activity["last_3_months"] > 0 and activity["prior_3_months"] > 0


def test_estimate_value_unknown_project():
    with pytest.raises(ValueError, match="search_projects"):
        PrivateResidentialService(FakeAnalysisRepository(profile=None)).estimate_value(
            ValuationQuery.from_inputs("nope", 900)
        )


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


def test_live_price_trend(live_service):
    filters = TransactionFilters.from_inputs(project="aurea", segment="auto")
    out = live_service.price_trend(filters, "quarter")
    assert out["points"] and out["segment"] is None
    assert sum(p["transactions"] for p in out["points"]) == 75  # all of AUREA's sales
    periods = [p["period"] for p in out["points"]]
    assert periods == sorted(periods)


def test_live_market_trend_defaults_to_non_landed(live_service):
    out = live_service.price_trend(TransactionFilters.from_inputs(segment="auto"), "year")
    assert out["segment"] == "non-landed" and len(out["points"]) >= 2


def test_live_nearby_by_distance(live_service):
    out = live_service.compare_nearby(
        NearbyQuery.from_inputs("treasure at tampines", radius_m=2000, months=24)
    )
    if out["basis"] != "radius":
        pytest.skip("project has no coordinates in this dataset")
    distances = [p["distance_m"] for p in out["peers"]]
    assert out["peers"] and distances == sorted(distances) and max(distances) <= 2000
    assert all("TREASURE" not in p["project"] for p in out["peers"])
    assert out["premium_pct"] is not None


def test_live_nearby_falls_back_for_project_without_coordinates(live_service):
    out = live_service.compare_nearby(NearbyQuery.from_inputs("aurea", months=12))
    assert out["basis"] in ("street", "district") and out["radius_m"] is None
    assert all(p["distance_m"] is None for p in out["peers"])


def test_live_nearby_unknown_project(live_service):
    with pytest.raises(ValueError, match="search_projects"):
        live_service.compare_nearby(NearbyQuery.from_inputs("no such project"))


def test_live_dataset_info(live_service):
    info = live_service.dataset_info()
    assert info["transactions"] > 0 and info["districts"] == sorted(info["districts"])
    assert sum(s["transactions"] for s in info["segments"]) == info["transactions"]
    assert {"non-landed", "landed"} <= {s["segment"] for s in info["segments"]}
    assert 0 < info["coordinate_coverage"]["transactions_pct"] <= 100


def test_live_project_summary_matches_other_tools(live_service):
    summary = live_service.project_summary("aurea")
    page = live_service.search_transactions(TransactionFilters.from_inputs(project="aurea"), 1)
    assert summary["transactions"] == page["total_matches"]
    mix = summary["sales_mix"]
    assert mix["new_sale"] + mix["sub_sale"] + mix["resale"] == summary["transactions"]
    assert summary["price"]["min"] <= summary["price"]["median"] <= summary["price"]["max"]
    assert summary["psf"]["p25"] <= summary["psf"]["median"] <= summary["psf"]["p75"]
    assert summary["segment"] == "non-landed" and summary["district"] == 7


def test_live_market_activity_matches_price_trend_counts(live_service):
    filters = TransactionFilters.from_inputs(project="aurea", segment="auto")
    activity = live_service.market_activity(filters, "quarter")
    trend = live_service.price_trend(filters, "quarter")
    assert activity["total_transactions"] == sum(p["transactions"] for p in trend["points"]) == 75
    assert all(
        p["transactions"] == p["new_sales"] + p["sub_sales"] + p["resales"]
        for p in activity["points"]
    )
    assert activity["points"][-1]["period"] == "2026-10-01"  # filled through the latest data month


def test_live_estimate_value_is_consistent_with_the_projects_own_prices(live_service):
    out = live_service.estimate_value(
        ValuationQuery.from_inputs("treasure at tampines", 1000, floor=12)
    )
    if out["estimate"] is None:
        pytest.skip("not enough sales in this dataset")
    assert out["model"]["mode"] == "project" and out["model"]["r_squared"] > 0.8
    estimate, check = out["estimate"], out["comparable_check"]
    assert estimate["low"] < estimate["median"] < estimate["high"]
    # a regression estimate and the project's own median psf should be in the same ballpark
    assert check["source"] == "project"
    assert estimate["psf"] == pytest.approx(check["median_psf"], rel=0.25)


def test_live_estimate_value_for_a_project_without_coordinates(live_service):
    out = live_service.estimate_value(
        ValuationQuery.from_inputs("aurea", 700, type_of_sale="new sale")
    )
    assert out["basis"] in ("street", "district")
    if out["estimate"] is not None:
        assert out["estimate"]["psf"] > 0 and out["unit"]["type_of_sale"] == "New Sale"
