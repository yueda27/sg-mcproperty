"""Elicitation: the dual-era helper, and the tools that ask for missing input.

The 2026-07-28 protocol has no server-initiated back-channel, so `ask` returns an
`InputRequiredResult` and FastMCP retries the tool with `ctx.input_responses`. Handshake-era
connections still call `ctx.elicit` directly. Every tool test runs on both eras.
"""

import asyncio
from dataclasses import dataclass

import pytest
from fastmcp import Client, Context, FastMCP
from fastmcp.client.elicitation import ElicitResult
from fastmcp.exceptions import ToolError
from fastmcp.server.elicitation import get_elicitation_schema
from mcp.types import InputRequiredResult

from sg_mcproperty.server.app import build_server
from sg_mcproperty.server.elicitation import ask
from sg_mcproperty.server.private_residential import max_affordable_price
from sg_mcproperty.server.tools.private_residential import UnitDetails

MODES = ("2026-07-28", "legacy")


def _run(coro):
    return asyncio.run(coro)


# --- the helper on its own ----------------------------------------------------------------------


@dataclass
class _Details:
    size: float
    floor: int = 0


def _demo_app() -> FastMCP:
    mcp = FastMCP("elicitation-demo")

    @mcp.tool
    async def demo(ctx: Context) -> dict | InputRequiredResult:
        answer = await ask(ctx, "details", "How big is the unit?", _Details)
        if answer.pending is not None:
            return answer.pending
        if answer.value is None:
            return {"action": answer.action}
        return {"action": "accept", "size": answer.value.size, "floor": answer.value.floor}

    return mcp


def _handler(payloads):
    async def handle(message, response_type, params, context):
        keys = frozenset(params.requested_schema["properties"])
        payload = payloads.get(keys)
        if payload is None:
            raise AssertionError(f"unexpected form: {sorted(keys)}")
        return payload

    return handle


async def _decline(message, response_type, params, context):
    return ElicitResult(action="decline")


async def _cancel(message, response_type, params, context):
    return ElicitResult(action="cancel")


@pytest.mark.parametrize("mode", MODES)
def test_helper_returns_the_answer_on_both_protocol_eras(mode):
    payloads = {frozenset({"size", "floor"}): {"size": 1100, "floor": 0}}

    async def run():
        async with Client(_demo_app(), mode=mode, elicitation_handler=_handler(payloads)) as client:
            return await client.call_tool("demo", {})

    assert _run(run()).data == {"action": "accept", "size": 1100.0, "floor": 0}


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("handler,action", [(_decline, "decline"), (_cancel, "cancel")])
def test_helper_reports_a_refusal_without_raising(mode, handler, action):
    async def run():
        async with Client(_demo_app(), mode=mode, elicitation_handler=handler) as client:
            return await client.call_tool("demo", {})

    assert _run(run()).data == {"action": action}


def test_forms_only_use_flat_elicitation_fields():
    # Elicitation schemas reject `int | None` union fields, so optional values use 0.
    schema = get_elicitation_schema(UnitDetails)
    assert schema["required"] == ["area_sqft"]
    assert schema["properties"]["floor"]["type"] == "integer"
    assert schema["properties"]["type_of_sale"]["enum"] == ["Resale", "New Sale"]


# --- tool flows, with a stub service ------------------------------------------------------------


def _value_estimate(query):
    return {
        "project": query.project,
        "segment": "non-landed",
        "basis": "radius",
        "radius_m": query.radius_m,
        "window_start": "2024-12-01",
        "window_end": "2026-11-01",
        "unit": {
            "area_sqft": query.area_sqft,
            "floor": query.floor,
            "type_of_sale": query.type_of_sale or "Resale",
            "remaining_lease_years": None,
            "freehold": True,
        },
        "estimate": None,
        "model": None,
        "effects": None,
        "comparable_check": None,
        "rental": None,
        "income_cross_check": None,
        "activity": {
            "sales_in_sample": 0,
            "last_3_months": 0,
            "prior_3_months": 0,
            "volume_change_pct": None,
        },
        "peers": [],
        "notes": [],
    }


def _nearby(query):
    return {
        "project": query.project,
        "segment": "non-landed",
        "basis": "radius",
        "radius_m": query.radius_m,
        "window_start": "2025-12-01",
        "window_end": "2026-11-01",
        "target": {
            "street": "MAIN ROAD",
            "district": 15,
            "property_type": "Condominium",
            "tenure": "99 years",
            "lease_year": 2010,
            "stats": None,
        },
        "peers": [],
        "peer_group": None,
        "premium_pct": None,
        "indicative_value": None,
        "rental": None,
        "notes": [],
    }


def _growth(query):
    return {
        "project": query.project,
        "segment": query.segment,
        "radius_m": query.radius_m,
        "window_start": "2021-12-01",
        "window_end": "2026-11-01",
        "size_band": {
            "min_area_sqft": query.min_area_sqft,
            "max_area_sqft": query.max_area_sqft,
        },
        "tenure_filter": query.tenure,
        "target": {
            "project": query.project,
            "street": "MAIN ROAD",
            "district": 15,
            "tenure": "99 years",
            "lease_year": 2010,
            "transactions": 10,
            "median_psf": 2000.0,
            "median_price": 2_000_000.0,
            "annual_growth_pct": 3.9,
        },
        "peers": [],
        "projects_compared": 0,
        "insufficient_data": 0,
        "ranking_basis": "annual_growth_pct",
        "rental": None,
        "notes": [],
    }


def _affordability(query, assumptions):
    return {
        "max_price": query.max_price,
        "assumptions": assumptions,
        "filters": {
            "district": query.district,
            "segment": query.segment,
            "min_area_sqft": query.min_area_sqft,
        },
        "projects": [],
        "notes": [],
    }


class StubService:
    def __init__(self):
        self.calls = []

    def estimate_value(self, query):
        self.calls.append(("estimate_value", query))
        return _value_estimate(query)

    def compare_nearby(self, query):
        self.calls.append(("compare_nearby", query))
        return _nearby(query)

    def rank_nearby_growth(self, query):
        self.calls.append(("rank_nearby_growth", query))
        return _growth(query)

    def affordability_search(self, query, assumptions):
        self.calls.append(("affordability_search", query, assumptions))
        return _affordability(query, assumptions)

    def income_budget(self, monthly_income, down_payment_pct, loan_tenure_years, stress_rate_pct):
        return max_affordable_price(
            monthly_income, down_payment_pct, loan_tenure_years, stress_rate_pct
        ), {
            "monthly_income": monthly_income,
            "down_payment_pct": down_payment_pct,
            "loan_tenure_years": loan_tenure_years,
            "stress_rate_pct": stress_rate_pct,
            "tdsr_pct": 55.0,
        }


UNIT_FORM = frozenset({"area_sqft", "floor", "type_of_sale"})
BASIS_FORM = frozenset({"type_of_sale", "same_tenure_type", "lease_year_tolerance"})
SCOPE_FORM = frozenset(
    {
        "radius_m",
        "max_peers",
        "months",
        "tenure",
        "min_area_sqft",
        "max_area_sqft",
        "min_transactions",
        "include_rental",
    }
)
PROFILE_FORM = frozenset(
    {
        "max_price",
        "monthly_income",
        "down_payment_pct",
        "loan_tenure_years",
        "stress_rate_pct",
        "district",
        "segment",
        "min_area_sqft",
    }
)


def _call(service, mode, payloads, tool, arguments):
    async def run():
        server = build_server(service)
        async with Client(server, mode=mode, elicitation_handler=_handler(payloads)) as client:
            return await client.call_tool(tool, arguments)

    return _run(run())


@pytest.mark.parametrize("mode", MODES)
def test_estimate_value_asks_for_size_floor_and_sale_type(mode):
    service = StubService()
    payloads = {
        UNIT_FORM: {"area_sqft": 1180, "floor": 12, "type_of_sale": "New Sale"},
    }
    _call(service, mode, payloads, "estimate_value", {"project": "aurea"})

    name, query = service.calls[0]
    assert name == "estimate_value"
    assert (query.area_sqft, query.floor, query.type_of_sale) == (1180, 12, "New Sale")


@pytest.mark.parametrize("mode", MODES)
def test_compare_nearby_asks_for_the_comparison_basis(mode):
    service = StubService()
    payloads = {
        BASIS_FORM: {
            "type_of_sale": "Resale",
            "same_tenure_type": True,
            "lease_year_tolerance": 10,
        },
    }
    _call(service, mode, payloads, "compare_nearby", {"project": "aurea", "ask_basis": True})

    _, query = service.calls[0]
    assert query.type_of_sale == "Resale"
    assert query.same_tenure_type is True
    assert query.lease_year_tolerance == 10


def test_compare_nearby_any_sale_type_means_no_filter():
    service = StubService()
    payloads = {
        BASIS_FORM: {
            "type_of_sale": "Any",
            "same_tenure_type": False,
            "lease_year_tolerance": 0,
        },
    }
    _call(
        service, "2026-07-28", payloads, "compare_nearby", {"project": "aurea", "ask_basis": True}
    )

    _, query = service.calls[0]
    assert query.type_of_sale is None and query.lease_year_tolerance is None


def test_compare_nearby_cancelling_keeps_the_arguments():
    service = StubService()

    async def run():
        async with Client(
            build_server(service), mode="2026-07-28", elicitation_handler=_cancel
        ) as client:
            return await client.call_tool(
                "compare_nearby",
                {"project": "aurea", "ask_basis": True, "type_of_sale": "New Sale"},
            )

    _run(run())
    _, query = service.calls[0]
    assert query.type_of_sale == "New Sale"


@pytest.mark.parametrize("mode", MODES)
def test_growth_scan_asks_for_its_scope(mode):
    service = StubService()
    payloads = {
        SCOPE_FORM: {
            "radius_m": 2500,
            "max_peers": 40,
            "months": 36,
            "tenure": "freehold or 999 years",
            "min_area_sqft": 1200,
            "max_area_sqft": 2000,
            "min_transactions": 5,
            "include_rental": True,
        },
    }
    _call(
        service,
        mode,
        payloads,
        "rank_nearby_growth",
        {"project": "aurea", "radius_m": 1000},
    )

    _, query = service.calls[0]
    assert (query.radius_m, query.max_peers, query.months) == (2500, 40, 36)
    assert query.tenure == "freehold or 999 years"
    assert (query.min_area_sqft, query.max_area_sqft) == (1200, 2000)
    assert query.min_transactions == 5
    assert query.include_rental is True


@pytest.mark.parametrize("mode", MODES)
def test_growth_scan_rental_question_keeps_the_passed_answer_by_default(mode):
    service = StubService()
    payloads = {
        SCOPE_FORM: {
            "radius_m": 2000,
            "max_peers": 50,
            "months": 60,
            "tenure": "any",
            "min_area_sqft": 0,
            "max_area_sqft": 0,
            "min_transactions": 4,
        },
    }
    _call(
        service,
        mode,
        payloads,
        "rank_nearby_growth",
        {"project": "aurea", "include_rental": True},
    )

    _, query = service.calls[0]
    assert query.include_rental is True


@pytest.mark.parametrize("mode", MODES)
def test_growth_scan_cancelling_keeps_the_arguments(mode):
    service = StubService()

    async def run():
        async with Client(build_server(service), mode=mode, elicitation_handler=_cancel) as client:
            return await client.call_tool(
                "rank_nearby_growth", {"project": "aurea", "radius_m": 1500, "months": 36}
            )

    _run(run())
    _, query = service.calls[0]
    assert (query.radius_m, query.months) == (1500, 36)


def test_growth_scan_runs_with_the_arguments_when_the_client_cannot_be_asked():
    # Handshake-era clients that never declared elicitation would raise if asked.
    service = StubService()

    async def run():
        async with Client(build_server(service), mode="legacy") as client:
            return await client.call_tool(
                "rank_nearby_growth", {"project": "aurea", "radius_m": 1500, "months": 36}
            )

    _run(run())
    _, query = service.calls[0]
    assert (query.radius_m, query.months) == (1500, 36)


@pytest.mark.parametrize("mode", MODES)
def test_affordability_search_asks_for_a_budget_or_income(mode):
    service = StubService()
    payloads = {
        PROFILE_FORM: {
            "max_price": 0,
            "monthly_income": 20_000,
            "down_payment_pct": 25,
            "loan_tenure_years": 25,
            "stress_rate_pct": 4.0,
            "district": 15,
            "segment": "non-landed",
            "min_area_sqft": 700,
        },
    }
    _call(service, mode, payloads, "affordability_search", {})

    _, query, assumptions = service.calls[0]
    assert query.max_price == max_affordable_price(20_000, 25, 25, 4.0)
    assert (query.district, query.min_area_sqft) == (15, 700)
    assert assumptions == {
        "monthly_income": 20_000.0,
        "down_payment_pct": 25.0,
        "loan_tenure_years": 25,
        "stress_rate_pct": 4.0,
        "tdsr_pct": 55.0,
    }


def test_affordability_search_accepts_a_budget_from_the_form():
    service = StubService()
    payloads = {
        PROFILE_FORM: {
            "max_price": 1_200_000,
            "monthly_income": 0,
            "down_payment_pct": 25,
            "loan_tenure_years": 25,
            "stress_rate_pct": 4.0,
            "district": 0,
            "segment": "non-landed",
            "min_area_sqft": 0,
        },
    }
    _call(service, "2026-07-28", payloads, "affordability_search", {})

    _, query, assumptions = service.calls[0]
    assert query.max_price == 1_200_000
    assert query.district is None and query.min_area_sqft is None
    assert assumptions is None


@pytest.mark.parametrize("mode", MODES)
def test_estimate_value_declining_leaves_nothing_to_value(mode):
    service = StubService()

    async def run():
        async with Client(build_server(service), mode=mode, elicitation_handler=_decline) as client:
            return await client.call_tool("estimate_value", {"project": "aurea"})

    with pytest.raises(ToolError, match="area_sqft"):
        _run(run())
    assert service.calls == []


def test_affordability_search_declining_is_an_error():
    service = StubService()

    async def run():
        async with Client(
            build_server(service), mode="2026-07-28", elicitation_handler=_decline
        ) as client:
            return await client.call_tool("affordability_search", {})

    with pytest.raises(ToolError, match="budget"):
        _run(run())
