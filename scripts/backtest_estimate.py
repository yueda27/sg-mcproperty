"""Backtest estimate_value: fit on older sales, predict held-out later sales, measure the error.

    python scripts/backtest_estimate.py --holdout-start 20 --projects 20

For each busy non-landed project with coordinates it fits the same regression estimate_value
uses on sales before month `holdout-start` (months are counted from the start of the 24 month
window) and predicts the project's own sales in the next 3 complete months. It reports the
median and mean absolute % error, the bias, how often the 80% interval covered the sale, and
the interval scale that would have given 80% coverage. Needs ClickHouse and the loaded data.
"""

import argparse
import math
import statistics

from sg_mcproperty.accessor import ClickHouseAccessor
from sg_mcproperty.server.private_residential import (
    PrivateResidentialRepository,
    PrivateResidentialService,
)
from sg_mcproperty.server.private_residential import regression as R
from sg_mcproperty.server.private_residential import service as S
from sg_mcproperty.server.private_residential.filters import PeerFilters, add_months, segment_of

MONTHS = 24
HOLDOUT_MONTHS = 3


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--holdout-start", type=int, default=20, help="first held-out month index")
    parser.add_argument("--projects", type=int, default=20)
    args = parser.parse_args()

    accessor = ClickHouseAccessor()
    repo = PrivateResidentialRepository(accessor)
    service = PrivateResidentialService(repo)
    last = repo.coverage()["last_month"].date()
    start = add_months(last, -(MONTHS - 1))
    cut = args.holdout_start
    end = min(cut + HOLDOUT_MONTHS - 1, MONTHS - 2)  # never test on the partial latest month

    projects = accessor.query(
        f"""SELECT project FROM {accessor.table("private_residential_transactions")}
        WHERE x IS NOT NULL AND property_type IN ('Apartment', 'Condominium')
          AND contract_date >= '{start}' AND area_sqft > 0
        GROUP BY project HAVING count() BETWEEN 60 AND 400
        ORDER BY count() DESC LIMIT {args.projects}"""
    )
    errors, covered, ratios, tested = [], 0, [], 0
    for row in projects:
        name = row["project"]
        profile = repo.project_profile(name)
        _, types = segment_of(profile["property_type"])
        filters = PeerFilters(
            property_types=types, start_date=start, min_area_sqft=300, max_area_sqft=4000
        )
        _, peers = service._find_peers(name, profile, 1500, filters, 12)
        sample = repo.valuation_sample([name, *(p["project"] for p in peers)], filters, 20000)
        sales = [s for r in sample if (s := S._sale_from_row(r, name, start, True)) is not None]
        train = [s for s in sales if s.month_index < cut]
        test = [s for s in sales if cut <= s.month_index <= end and s.is_target]
        own = sum(s.is_target for s in train)
        if len(train) < S.MIN_OBSERVATIONS or not test:
            continue
        model = R.fit(train, True, project_effects=own >= S.MIN_TARGET_SALES)
        if model is None:
            continue
        for sale in test:
            unit = R.Unit(
                sale.area_sqft, sale.floor, cut - 1, sale.new_sale, sale.freehold,
                sale.remaining_lease, True,
            )  # fmt: skip
            mean, se = R.predict(model, unit, True)
            error = math.log(sale.price) - mean
            errors.append(error)
            ratios.append(abs(error) / se)
            low, high = R.interval(0.0, se)  # interval around zero error
            covered += low <= error <= high
            tested += 1

    if not tested:
        print("no held-out sales: check ClickHouse and the loaded data")
        return
    absolute = sorted(abs(math.exp(e) - 1) * 100 for e in errors)
    needed = sorted(ratios)[int(0.8 * len(ratios))] / R.Z_80
    print(f"held-out sales: {tested} across {len(projects)} projects (months {cut}-{end})")
    print(
        f"median abs error {statistics.median(absolute):.1f}%  mean {statistics.mean(absolute):.1f}%"
        f"  90th pct {absolute[int(0.9 * len(absolute))]:.1f}%"
    )
    print(f"bias (mean signed error) {(math.exp(statistics.mean(errors)) - 1) * 100:+.1f}%")
    print(
        f"80% interval coverage {covered / tested:.1%} with INTERVAL_SCALE {R.INTERVAL_SCALE} "
        f"(a scale of {needed:.2f} would give exactly 80% on this period)"
    )


if __name__ == "__main__":
    main()
