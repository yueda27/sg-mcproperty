"""Known-answer tests for the rental maths: no fakes, no database."""

import pytest

from sg_mcproperty.server.private_rental import returns


def test_area_bounds_are_plus_minus_twenty_percent():
    low, high = returns.area_bounds(1905)
    assert (low, high) == (1524.0, 2286.0)


def test_area_bounds_rejects_a_non_positive_reference():
    with pytest.raises(ValueError, match="positive"):
        returns.area_bounds(0)


def test_gross_yield_is_annual_rent_psf_over_sale_psf():
    # Duchess Residences, 1,905 sqft band: rent 5.03 psf/month, sale 2,414 psf.
    assert returns.gross_yield_pct(5.03, 2414) == 2.5


def test_gross_yield_is_null_when_either_side_is_missing_or_zero():
    assert returns.gross_yield_pct(None, 2414) is None
    assert returns.gross_yield_pct(5.03, None) is None
    assert returns.gross_yield_pct(0, 2414) is None
    assert returns.gross_yield_pct(5.03, 0) is None


def test_annual_rent_for_the_unit():
    assert returns.annual_rent(5.03, 1905) == 114_986
    assert returns.annual_rent(None, 1905) is None
    assert returns.annual_rent(5.03, 0) is None


def test_total_return_adds_the_yield_to_realised_growth():
    assert returns.total_return_pct(2.43, 2.6) == 5.0
    assert returns.total_return_pct(3.01, 4.7) == 7.7


def test_total_return_is_null_when_either_leg_is_missing():
    assert returns.total_return_pct(None, 2.6) is None
    assert returns.total_return_pct(2.43, None) is None


def test_implied_value_capitalises_rent_at_a_peer_yield():
    assert returns.implied_value(114_986, 2.9) == 3_965_000
    assert returns.implied_value(None, 2.9) is None
    assert returns.implied_value(114_986, None) is None
    assert returns.implied_value(114_986, 0) is None


def test_divergence_is_signed_and_relative():
    assert returns.divergence_pct(3_965_000, 4_088_000) == -3.0
    assert returns.divergence_pct(4_088_000, 4_088_000) == 0.0
    assert returns.divergence_pct(None, 4_088_000) is None
    assert returns.divergence_pct(4_088_000, None) is None


def test_plausible_yield_guards_against_mismatched_pairs():
    assert returns.plausible_yield(2.5)
    assert not returns.plausible_yield(0.2)
    assert not returns.plausible_yield(12.0)
    assert not returns.plausible_yield(None)
