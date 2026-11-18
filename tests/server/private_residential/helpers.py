"""Fakes and row builders shared by the private residential tests."""

from datetime import datetime


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
