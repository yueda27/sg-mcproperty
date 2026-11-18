"""Hedonic regression for valuing a private residential unit. Pure code: no database, no MCP.

The model is ordinary least squares on log price, fitted on recent sales of the target
project and similar projects nearby. Coefficients on log price read as percentage effects. Two
modes:

"project" (the target has enough sales of its own). One level per project (fixed effects):

    ln(price) = level[project] + b1*ln(area) + b2*floor/10 + b3*new_sale + b4*month

  The slopes come from variation *within* projects, so project quality (location, facilities,
  age) cannot leak into them. The prediction uses the target's own level. Lease and freehold
  are not estimated here: they are constant within a project and absorbed by its level.

"area" (the target has too few sales). Cross-project model, no project levels:

    ln(price) = b0 + b1*ln(area) + b2*floor/10 + b3*remaining_lease/10 + b4*freehold
                + b5*new_sale + b6*month

  The lease and freehold effects are then identified by differences between projects, so they
  can be confounded with project quality: less reliable, but the only option without own sales.

Features that do not vary in the sample (all freehold, no floor data, ...) are dropped, so the
model always fits whatever the sample can support.

Hand-rolled with plain Python on purpose: samples are a few hundred to a few thousand rows and
at most eight columns, which does not justify a numpy runtime dependency.
"""

import math
from dataclasses import dataclass

# Candidate features in design-matrix order. Continuous ones are centred on the sample mean so
# the intercept is the log price of an average sale and the normal equations stay well scaled.
FEATURES = ("ln_area", "floor_10", "lease_10", "freehold", "new_sale", "month")
PROJECT_FEATURES = ("ln_area", "floor_10", "new_sale", "month")
PROJECT_PREFIX = "project:"
CENTRED = {"ln_area", "floor_10", "lease_10", "month"}

Z_80 = 1.2816  # two-sided 80% interval under normal residuals
# The model assumes the market's direction is known exactly, which out of sample it is not. In
# backtests (scripts/backtest_estimate.py, four hold-out periods, about 1,200 held-out sales)
# the plain 80% interval under-covered. With this 15% widening coverage was 74-85% by period and
# 81% pooled; the most recent period was the worst (74%, in a rising market).
INTERVAL_SCALE = 1.15
OUTLIER_SDS = 3.0  # drop sales further than this many residual SDs from the first fit
MAX_OUTLIER_SHARE = 0.05
_CONSTANT = 1e-12  # variance below this means the feature does not vary


@dataclass(frozen=True)
class Unit:
    """The attributes of one unit. `month_index` counts months from the start of the window."""

    area_sqft: float
    floor: int | None
    month_index: int
    new_sale: bool
    freehold: bool
    remaining_lease: float  # years, capped at 99 by the caller
    is_target: bool


@dataclass(frozen=True)
class Sale(Unit):
    price: float = 0.0
    project: str = ""


@dataclass(frozen=True)
class Model:
    mode: str  # "project" or "area"
    names: tuple[str, ...]  # "intercept" first, then the features that were used
    coefficients: tuple[float, ...]
    means: dict[str, float]
    residual_sd: float  # in log points, roughly the typical fractional error
    r_squared: float
    observations: int
    outliers_removed: int
    xtx_inverse: tuple[tuple[float, ...], ...]

    def coefficient(self, feature: str) -> float | None:
        return dict(zip(self.names, self.coefficients)).get(feature)

    def project_levels(self) -> dict[str, float]:
        """Each peer project's log price level relative to the target (project mode)."""
        return {
            name.removeprefix(PROJECT_PREFIX): value
            for name, value in zip(self.names, self.coefficients)
            if name.startswith(PROJECT_PREFIX)
        }


def _features(unit: Unit, use_floor: bool) -> dict[str, float]:
    values = {
        "ln_area": math.log(unit.area_sqft),
        "lease_10": unit.remaining_lease / 10,
        "freehold": float(unit.freehold),
        "new_sale": float(unit.new_sale),
        "month": float(unit.month_index),
    }
    if use_floor and unit.floor is not None:
        values["floor_10"] = unit.floor / 10
    return values


def _invert(matrix: list[list[float]]) -> list[list[float]] | None:
    """Gauss-Jordan inverse with partial pivoting. None if the matrix is (near) singular."""
    n = len(matrix)
    scale = max(abs(matrix[i][i]) for i in range(n)) or 1.0
    aug = [row[:] + [1.0 if i == j else 0.0 for j in range(n)] for i, row in enumerate(matrix)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(aug[r][col]))
        if abs(aug[pivot][col]) < 1e-10 * scale:
            return None
        aug[col], aug[pivot] = aug[pivot], aug[col]
        divisor = aug[col][col]
        aug[col] = [v / divisor for v in aug[col]]
        for r in range(n):
            if r != col and aug[r][col] != 0.0:
                factor = aug[r][col]
                aug[r] = [a - factor * b for a, b in zip(aug[r], aug[col])]
    return [row[n:] for row in aug]


def _ols(
    rows: list[list[float]], y: list[float]
) -> tuple[list[float], list[float], list[list[float]]] | None:
    """Least squares via the normal equations. Returns (coefficients, residuals, (X'X)^-1)."""
    k = len(rows[0])
    xtx = [[0.0] * k for _ in range(k)]
    xty = [0.0] * k
    for row, target in zip(rows, y):
        for i in range(k):
            xty[i] += row[i] * target
            for j in range(i, k):
                xtx[i][j] += row[i] * row[j]
    for i in range(k):
        for j in range(i):
            xtx[i][j] = xtx[j][i]
    inverse = _invert(xtx)
    if inverse is None:
        return None
    beta = [sum(inverse[i][j] * xty[j] for j in range(k)) for i in range(k)]
    residuals = [t - sum(b * v for b, v in zip(beta, row)) for row, t in zip(rows, y)]
    return beta, residuals, inverse


def _choose_features(
    sales: list[Sale], use_floor: bool, project_effects: bool
) -> tuple[list[str], dict[str, list[float]]]:
    """The features that vary in the sample, with their raw values per sale."""
    raw = [_features(s, use_floor) for s in sales]
    candidates: dict[str, list[float]] = {}
    for name in PROJECT_FEATURES if project_effects else FEATURES:
        if all(name in r for r in raw):  # a missing name means no floor data
            candidates[name] = [r[name] for r in raw]
    if project_effects:
        # One 0/1 column per peer project. The target is the baseline (all zeros).
        for project in sorted({s.project for s in sales if not s.is_target}):
            candidates[PROJECT_PREFIX + project] = [float(s.project == project) for s in sales]

    columns: dict[str, list[float]] = {}
    for name, values in candidates.items():
        mean = sum(values) / len(values)
        if sum((v - mean) ** 2 for v in values) / len(values) >= _CONSTANT:
            columns[name] = values  # drop features that do not vary: they cannot be estimated
    return list(columns), columns


def fit(sales: list[Sale], use_floor: bool, project_effects: bool = False) -> Model | None:
    """Fit the model, drop gross outliers once, and refit. None if it cannot be fitted."""
    names, columns = _choose_features(sales, use_floor, project_effects)
    means = {n: sum(columns[n]) / len(sales) for n in names if n in CENTRED}

    def design(index: list[int]) -> list[list[float]]:
        return [[1.0] + [columns[n][i] - means.get(n, 0.0) for n in names] for i in index]

    y_all = [math.log(s.price) for s in sales]
    keep = list(range(len(sales)))
    result = _ols(design(keep), [y_all[i] for i in keep])
    if result is None or len(keep) <= len(names) + 1:
        return None
    _, residuals, _ = result
    sd = math.sqrt(sum(e * e for e in residuals) / (len(keep) - len(names) - 1))
    removed = 0
    outliers = [i for i, e in zip(keep, residuals) if abs(e) > OUTLIER_SDS * sd]
    if outliers and len(outliers) <= MAX_OUTLIER_SHARE * len(keep):
        dropped = set(outliers)
        keep = [i for i in keep if i not in dropped]
        removed = len(dropped)
        result = _ols(design(keep), [y_all[i] for i in keep])
        if result is None or len(keep) <= len(names) + 1:
            return None

    beta, residuals, inverse = result
    y = [y_all[i] for i in keep]
    sse = sum(e * e for e in residuals)
    mean_y = sum(y) / len(y)
    sst = sum((v - mean_y) ** 2 for v in y)
    return Model(
        mode="project" if project_effects else "area",
        names=("intercept", *names),
        coefficients=tuple(beta),
        means=means,
        residual_sd=math.sqrt(sse / (len(keep) - len(names) - 1)),
        r_squared=1 - sse / sst if sst > 0 else 0.0,
        observations=len(keep),
        outliers_removed=removed,
        xtx_inverse=tuple(tuple(row) for row in inverse),
    )


def predict(model: Model, unit: Unit, use_floor: bool) -> tuple[float, float]:
    """Predicted log price for `unit` and the standard error of a single new sale there."""
    raw = _features(unit, use_floor)
    # project columns are 0 for the target unit, which is the baseline in project mode
    x = [1.0] + [raw.get(n, 0.0) - model.means.get(n, 0.0) for n in model.names[1:]]
    mean = sum(b * v for b, v in zip(model.coefficients, x))
    leverage = sum(
        x[i] * model.xtx_inverse[i][j] * x[j] for i in range(len(x)) for j in range(len(x))
    )
    return mean, model.residual_sd * math.sqrt(1 + leverage)


def interval(log_price: float, standard_error: float) -> tuple[float, float]:
    """The 80% prediction interval for a log price, widened by the backtested INTERVAL_SCALE."""
    half_width = Z_80 * INTERVAL_SCALE * standard_error
    return log_price - half_width, log_price + half_width
