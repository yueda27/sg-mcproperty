"""Fakes and row builders shared by the private residential tests."""

import math
import random
from datetime import date, datetime


class FakeAccessor:
    """Records SQL and parameters, replies with canned rows (one list per call)."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def table(self, name):
        return f"db.{name}"

    def query(self, sql, parameters=None):
        self.calls.append((sql, parameters))
        return self.responses.pop(0) if self.responses else []


class FakeRepository:
    """Stands in for the repository so service tests never see SQL."""

    def __init__(self, projects=(), fuzzy=(), transactions=(), total=0, coverage=None):
        self.projects, self.fuzzy = list(projects), list(fuzzy)
        self.transactions, self.total = list(transactions), total
        self._coverage = coverage
        self.calls = []

    def find_projects(self, query, tokens, district, limit):
        self.calls.append(("find_projects", query, tokens, district, limit))
        return self.projects

    def find_projects_fuzzy(self, query, tokens, district, limit):
        self.calls.append(("find_projects_fuzzy", query, tokens, district, limit))
        return self.fuzzy

    def count_transactions(self, filters):
        return self.total

    def list_transactions(self, filters, limit):
        self.calls.append(("list_transactions", filters, limit))
        return self.transactions

    def coverage(self):
        return self._coverage


def project_row(**overrides):
    row = {
        "project": "PARKTOWN RESIDENCE",
        "street": "TAMPINES STREET 62",
        "district": 18,
        "tenure": "99 years",
        "lease_year": 2023,
        "transactions": 10,
        "first_sale": datetime(2023, 1, 1),
        "last_sale": datetime(2026, 4, 1),
        "match_type": "prefix",
    }
    return {**row, **overrides}


def transaction_row(**overrides):
    row = {
        "project": "AUREA",
        "street": "BEACH ROAD",
        "district": 7,
        "contract_date": datetime(2026, 11, 1),
        "type_of_sale": "New Sale",
        "price": 3_000_000.0,
        "area_sqft": 1000.0,
        "psf": 3000.0,
        "floor": 8,
        "tenure": "99 years",
        "lease_year": 2024,
    }
    return {**row, **overrides}


def stats_row(
    transactions=10, projects=1, p25=1800.0, median=2000.0, p75=2200.0, price=2_000_000.0
):
    return {
        "transactions": transactions,
        "projects": projects,
        "p25_psf": p25,
        "median_psf": median,
        "p75_psf": p75,
        "median_price": price,
    }


def profile_row(**overrides):
    row = {
        "project": "TARGET",
        "street": "MAIN ROAD",
        "district": 15,
        "property_type": "Condominium",
        "tenure": "99 years",
        "lease_year": 2018,
        "transactions": 100,
        "x": 30000.0,
        "y": 33000.0,
    }
    return {**row, **overrides}


def peer_row(project="PEER A", distance_m=500, **overrides):
    row = {
        "project": project,
        "street": "OTHER ROAD",
        "district": 15,
        "property_type": "Condominium",
        "tenure": "99 years",
        "lease_year": 2016,
        "transactions": 20,
        "median_psf": 1900.0,
        "median_price": 1_900_000.0,
        "distance_m": distance_m,
    }
    return {**row, **overrides}


def facets_row(**overrides):
    row = {
        "districts": [1, 2, 3],
        "types_of_sale": ["New Sale", "Resale", "Sub Sale"],
        "projects_with_coordinates_pct": 95.0,
        "transactions_with_coordinates_pct": 80.0,
    }
    return {**row, **overrides}


def summary_row(**overrides):
    row = {
        "transactions": 75,
        "first_sale": datetime(2025, 3, 1),
        "last_sale": datetime(2026, 11, 1),
        "new_sales": 70,
        "sub_sales": 1,
        "resales": 4,
        "min_price": 1_700_000.0,
        "median_price": 2_000_000.0,
        "max_price": 12_000_000.0,
        "p25_psf": 2700.0,
        "median_psf": 2900.0,
        "p75_psf": 3000.0,
        "min_area_sqft": 600.0,
        "median_area_sqft": 700.0,
        "max_area_sqft": 3000.0,
        "recent_transactions": 30,
        "recent_median_psf": 3000.0,
        "prior_transactions": 40,
        "prior_median_psf": 2900.0,
    }
    return {**row, **overrides}


def synthetic_sample(
    levels, per_project=40, seed=3, noise=0.03, months=24, start=date(2024, 12, 1)
):
    """Sales with known structure: ln price = level[project] + 0.9 ln(area) + 0.03 floor/10
    + 0.004 month + 0.07 new_sale + noise. `levels` maps project -> log price level."""
    rng = random.Random(seed)
    rows = []
    for project, level in levels.items():
        for _ in range(per_project):
            area = rng.uniform(600, 1500)
            floor = rng.randint(1, 30)
            month = rng.randint(0, months - 2)
            new_sale = rng.random() < 0.2
            ln_price = (
                level
                + 0.9 * math.log(area)
                + 0.03 * floor / 10
                + 0.004 * month
                + 0.07 * new_sale
                + rng.gauss(0, noise)
            )
            index = start.year * 12 + start.month - 1 + month
            rows.append(
                {
                    "project": project,
                    "area_sqft": area,
                    "floor": floor,
                    "price": math.exp(ln_price),
                    "contract_date": datetime(index // 12, index % 12 + 1, 1),
                    "type_of_sale": "New Sale" if new_sale else "Resale",
                    "tenure": "99 years",
                    "lease_year": 2015,
                }
            )
    return rows


def activity_row(period, transactions=10, new=0, sub=0, resale=None, psf=2000.0):
    return {
        "period": period,
        "transactions": transactions,
        "new_sales": new,
        "sub_sales": sub,
        "resales": transactions - new - sub if resale is None else resale,
        "median_price": psf * 1000.0,
        "median_psf": psf,
    }


def growth_row(project="PEER A", distance_m=500, growth=0.045, transactions=8, **overrides):
    row = {
        "project": project,
        "street": "OTHER ROAD",
        "district": 15,
        "tenure": "99 years",
        "lease_year": 2010,
        "property_type": "Condominium",
        "distance_m": distance_m,
        "transactions": transactions,
        "median_psf": 2000.0,
        "median_price": 2_000_000.0,
        "annual_growth": growth,
    }
    return {**row, **overrides}


def project_growth_row(growth=0.039, transactions=12, **overrides):
    row = {
        "transactions": transactions,
        "median_psf": 2100.0,
        "median_price": 2_100_000.0,
        "annual_growth": growth,
    }
    return {**row, **overrides}


def affordable_row(project="AFFORDABLE", **overrides):
    row = {
        "project": project,
        "street": "VALUE ROAD",
        "district": 15,
        "transactions": 5,
        "median_price": 1_500_000.0,
        "min_price": 1_200_000.0,
        "median_psf": 1500.0,
        "min_area_sqft": 700.0,
        "max_area_sqft": 1100.0,
    }
    return {**row, **overrides}


class FakeAnalysisRepository:
    """Repository stand-in for price_trend and compare_nearby. Records the calls made."""

    def __init__(
        self,
        trend=(),
        profile=None,
        distance_peers=(),
        street_peers=(),
        district_peers=(),
        target=None,
        pooled=None,
        facets=None,
        type_counts=(),
        summary=None,
        sample=(),
        activity=(),
        growth_peers=(),
        growth_target=None,
        affordable=(),
    ):
        self.trend = list(trend)
        self.profile = profile
        self.distance_peers = list(distance_peers)
        self.street_peers = list(street_peers)
        self.district_peers = list(district_peers)
        self.target = target or stats_row(transactions=30, median=2400.0)
        self.pooled = pooled or stats_row(transactions=60, projects=3)
        self.facets = facets or facets_row()
        self.type_counts = list(type_counts)
        self.summary = summary or summary_row()
        self.sample = list(sample)
        self.activity = list(activity)
        self.growth_peers = list(growth_peers)
        self.growth_target = growth_target or project_growth_row()
        self.affordable = list(affordable)
        self.calls = []

    def price_trend(self, filters, period_fn):
        self.calls.append(("price_trend", filters, period_fn))
        return self.trend

    def project_profile(self, project):
        self.calls.append(("project_profile", project))
        return self.profile

    def coverage(self):
        return {
            "transactions": 1,
            "projects": 1,
            "first_month": datetime(2021, 11, 1),
            "last_month": datetime(2026, 11, 1),
        }

    def find_peers_by_distance(self, project, x, y, radius_m, filters, min_transactions, limit):
        self.calls.append(("distance", x, y, radius_m, filters, limit))
        return self.distance_peers

    def find_peers_by_area(self, project, scope, value, filters, min_transactions, limit):
        self.calls.append((scope, value, filters, limit))
        return self.street_peers if scope == "street" else self.district_peers

    def project_stats(self, project, filters):
        self.calls.append(("project_stats", project, filters))
        return self.target

    def pooled_stats(self, projects, filters):
        self.calls.append(("pooled_stats", projects, filters))
        return self.pooled

    def dataset_facets(self):
        return self.facets

    def property_type_counts(self):
        return self.type_counts

    def project_summary(self, project, recent_start, prior_start):
        self.calls.append(("project_summary", project, recent_start, prior_start))
        return self.summary

    def valuation_sample(self, projects, filters, limit):
        self.calls.append(("valuation_sample", projects, filters, limit))
        return self.sample

    def market_activity(self, filters, period_fn):
        self.calls.append(("market_activity", filters, period_fn))
        return self.activity

    def nearby_growth(self, query, x, y):
        self.calls.append(("nearby_growth", query, x, y))
        return self.growth_peers

    def project_growth(self, query):
        self.calls.append(("project_growth", query))
        return self.growth_target

    def affordable_projects(self, query):
        self.calls.append(("affordable_projects", query))
        return self.affordable
