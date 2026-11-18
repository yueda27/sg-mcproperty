"""Rental yield and total-return maths. Pure code: no database, no MCP.

A gross rental yield compares two medians measured on the *same* population of units:

    gross_yield_pct = 12 * rent_psf_month / sale_psf * 100

`rent_psf_month` is rent per sqft per month and `sale_psf` is the resale price per sqft. Both
sides must be restricted to a size band around one reference area, because rent psf falls with
unit size (a large unit rents for less per sqft than a small one). Comparing project-wide
averages would mostly rank unit mix, not value.

The windows differ on purpose. Rents reprice on renewal, so a five-year rent median understates
today's rent after a surge; a year is enough for a stable median. Sale prices are stickier, so
two years buys sample without going stale.

Total return is the gross income return plus the *realised* capital return:

    gross_total_return_pct = gross_yield_pct + annual_capital_growth_pct

That mixes a current yield with a past growth rate, so it reads as "what this asset returned
lately", never as a forecast. It is before stamp duty, agent fees, property tax, maintenance,
vacancy and income tax.
"""

MONTHS_PER_YEAR = 12

YIELD_SALE_MONTHS = 24  # sale psf behind the yield: recent, but enough sales to median
YIELD_RENT_MONTHS = 12  # rent median: a year, so a past surge is not averaged away
MATCH_BAND = 0.20  # +/-20% of the reference area, the same band used for comparable sales

MIN_RENTALS = 3  # rentals in the band needed before a rent median is trusted
# Two resales is thin, but a low-turnover project (999-year, large units) can sell only twice in
# a +/-20% band over two years; refusing a yield there defeats the purpose. The counts are always
# returned so the caller can say the price side is thin.
MIN_SALES = 2
MIN_PEER_PROJECTS = 3  # peers with a yield needed before an income cross-check is offered

# A gross yield outside this range almost always means a mismatched pair (a project's rents
# paired with another project's prices, or a mixed tenure) rather than a real return.
PLAUSIBLE_YIELD_RANGE = (0.5, 8.0)


def area_bounds(reference_sqft: float) -> tuple[float, float]:
    """The size window a yield is measured on: +/-20% of the reference area."""
    if reference_sqft <= 0:
        raise ValueError("reference_sqft must be positive")
    return (
        reference_sqft * (1 - MATCH_BAND),
        reference_sqft * (1 + MATCH_BAND),
    )


def annual_rent(rent_psf_month: float | None, area_sqft: float) -> int | None:
    """Annual rent in SGD for a unit of `area_sqft` at `rent_psf_month`."""
    if rent_psf_month is None or area_sqft <= 0:
        return None
    return round(rent_psf_month * MONTHS_PER_YEAR * area_sqft)


def gross_yield_pct(rent_psf_month: float | None, sale_psf: float | None) -> float | None:
    """Gross rental yield in percent: annual rent psf over sale psf."""
    if rent_psf_month is None or sale_psf is None:
        return None
    if rent_psf_month <= 0 or sale_psf <= 0:
        return None
    return round(rent_psf_month * MONTHS_PER_YEAR / sale_psf * 100, 2)


def plausible_yield(gross_yield: float | None) -> bool:
    """True when a gross yield is inside the range a real project could plausibly show."""
    if gross_yield is None:
        return False
    low, high = PLAUSIBLE_YIELD_RANGE
    return low <= gross_yield <= high


def total_return_pct(
    gross_yield: float | None, annual_capital_growth: float | None
) -> float | None:
    """Gross total return: current gross yield plus realised annual capital growth."""
    if gross_yield is None or annual_capital_growth is None:
        return None
    return round(gross_yield + annual_capital_growth, 1)


def implied_value(annual_rent_sgd: float | None, peer_yield_pct: float | None) -> int | None:
    """Income-approach value: annual rent capitalised at a peer gross yield."""
    if annual_rent_sgd is None or peer_yield_pct is None or peer_yield_pct <= 0:
        return None
    return int(round(annual_rent_sgd / (peer_yield_pct / 100), -3))


def divergence_pct(value: float | None, reference: float | None) -> float | None:
    """How far `value` sits from `reference`, in percent. Positive means above."""
    if value is None or reference is None or reference <= 0:
        return None
    return round((value / reference - 1) * 100, 1)
