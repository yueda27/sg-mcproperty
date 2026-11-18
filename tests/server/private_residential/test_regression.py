import math
import random

import pytest

from sg_mcproperty.server.private_residential import regression as R

TRUE = {
    "ln_area": 0.9,
    "floor_10": 0.03,
    "lease_10": 0.04,
    "freehold": 0.10,
    "new_sale": 0.08,
    "month": 0.004,
}


def area_sales(n=400, noise=0.0, seed=7):
    """Cross-project data with known coefficients (no project effects)."""
    rng = random.Random(seed)
    sales = []
    for _ in range(n):
        area, floor = rng.uniform(500, 1800), rng.randint(1, 30)
        freehold, new_sale, month = rng.random() < 0.2, rng.random() < 0.3, rng.randint(0, 23)
        lease = 99.0 if freehold else rng.uniform(40, 99)
        ln_price = (
            12.0
            + TRUE["ln_area"] * math.log(area)
            + TRUE["floor_10"] * floor / 10
            + TRUE["lease_10"] * lease / 10
            + TRUE["freehold"] * freehold
            + TRUE["new_sale"] * new_sale
            + TRUE["month"] * month
            + rng.gauss(0, noise)
        )
        sales.append(
            R.Sale(area, floor, month, new_sale, freehold, lease, False, math.exp(ln_price), "P")
        )
    return sales


def project_sales(levels, per_project=60, noise=0.0, seed=5):
    """Data with a different price level per project and shared slopes."""
    rng = random.Random(seed)
    sales = []
    for project, level in levels.items():
        for _ in range(per_project):
            area, floor, month = rng.uniform(500, 1800), rng.randint(1, 30), rng.randint(0, 23)
            new_sale = rng.random() < 0.3
            ln_price = (
                level + 0.9 * math.log(area) + 0.03 * floor / 10 + 0.08 * new_sale
                + 0.004 * month + rng.gauss(0, noise)
            )  # fmt: skip
            sales.append(
                R.Sale(area, floor, month, new_sale, False, 80.0, project == "TARGET",
                       math.exp(ln_price), project)
            )  # fmt: skip
    return sales


def test_area_mode_recovers_known_coefficients_exactly():
    model = R.fit(area_sales(), use_floor=True)
    assert model.mode == "area" and model.r_squared == pytest.approx(1.0)
    assert model.residual_sd < 1e-9
    for feature, truth in TRUE.items():
        assert model.coefficient(feature) == pytest.approx(truth, abs=1e-8)


def test_area_mode_recovers_coefficients_with_noise():
    model = R.fit(area_sales(n=1500, noise=0.05), use_floor=True)
    for feature, truth in TRUE.items():
        assert model.coefficient(feature) == pytest.approx(truth, abs=0.02)
    assert 0.04 < model.residual_sd < 0.06


def test_project_mode_recovers_slopes_and_levels_relative_to_target():
    levels = {"TARGET": 10.0, "A": 10.3, "B": 9.8}
    model = R.fit(project_sales(levels), use_floor=True, project_effects=True)
    assert model.mode == "project"
    for feature, truth in (
        ("ln_area", 0.9),
        ("floor_10", 0.03),
        ("new_sale", 0.08),
        ("month", 0.004),
    ):
        assert model.coefficient(feature) == pytest.approx(truth, abs=1e-8)
    # levels are relative to the target, the baseline
    assert model.project_levels() == pytest.approx({"A": 0.3, "B": -0.2}, abs=1e-8)
    # lease and freehold are not estimated in project mode (absorbed by the project level)
    assert model.coefficient("lease_10") is None and model.coefficient("freehold") is None


def test_project_mode_slopes_are_not_confounded_by_project_quality():
    # Bigger units sell in the dearer project: pooled slope would be biased, within-project is not.
    rng = random.Random(1)
    sales = []
    for project, level, lo, hi in (("TARGET", 10.0, 500, 900), ("DEAR", 11.0, 1000, 1800)):
        for _ in range(80):
            area = rng.uniform(lo, hi)
            sales.append(R.Sale(area, 10, 5, False, False, 80.0, project == "TARGET",
                                math.exp(level + 0.9 * math.log(area)), project))  # fmt: skip
    pooled = R.fit(sales, use_floor=False)
    fixed = R.fit(sales, use_floor=False, project_effects=True)
    assert fixed.coefficient("ln_area") == pytest.approx(0.9, abs=1e-8)
    assert abs(pooled.coefficient("ln_area") - 0.9) > 0.3


def test_prediction_matches_the_data_and_target_is_the_baseline():
    sales = project_sales({"TARGET": 10.0, "A": 10.3})
    model = R.fit(sales, use_floor=True, project_effects=True)
    unit = R.Unit(1000.0, 12, 23, False, False, 80.0, True)
    mean, se = R.predict(model, unit, use_floor=True)
    truth = 10.0 + 0.9 * math.log(1000) + 0.03 * 1.2 + 0.004 * 23
    assert mean == pytest.approx(truth, abs=1e-8) and se < 1e-6


def test_constant_features_are_dropped():
    sales = [s for s in area_sales() if not s.freehold]  # nobody is freehold
    model = R.fit(sales, use_floor=True)
    assert "freehold" not in model.names
    no_floor = R.fit(area_sales(), use_floor=False)
    assert "floor_10" not in no_floor.names


def test_gross_outliers_are_trimmed_and_counted():
    sales = area_sales(n=300, noise=0.02)
    wild = [R.Sale(1000.0, 10, 10, False, False, 80.0, False, 1e12, "P")]
    model = R.fit(sales + wild, use_floor=True)
    assert model.outliers_removed == 1
    assert model.coefficient("ln_area") == pytest.approx(0.9, abs=0.03)


def test_unfittable_inputs_return_none():
    assert R.fit(area_sales(n=5), use_floor=True) is None
    identical = [R.Sale(900.0, 5, 0, False, False, 80.0, False, 1e6, "P")] * 50
    constant = R.fit(identical, use_floor=True)
    assert constant.names == ("intercept",)  # nothing varies: only a level is estimated


def test_interval_is_symmetric_and_widened_by_the_backtested_scale():
    low, high = R.interval(14.0, 0.05)
    half = R.Z_80 * R.INTERVAL_SCALE * 0.05
    assert (low, high) == pytest.approx((14.0 - half, 14.0 + half))
    assert R.INTERVAL_SCALE > 1.0


def test_interval_covers_about_80_percent_on_fresh_noisy_data():
    model = R.fit(area_sales(n=800, noise=0.06, seed=1), use_floor=True)
    fresh = area_sales(n=2000, noise=0.06, seed=2)
    hits = 0
    for s in fresh:
        unit = R.Unit(
            s.area_sqft, s.floor, s.month_index, s.new_sale, s.freehold, s.remaining_lease, False
        )
        mean, se = R.predict(model, unit, use_floor=True)
        low, high = R.interval(mean, se)
        hits += low <= math.log(s.price) <= high
    assert 0.80 < hits / len(fresh) < 0.90  # wider than 80% because of the 1.15 scale
