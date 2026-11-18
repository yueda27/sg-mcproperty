import asyncio
import json
from datetime import datetime

from fastmcp import Client

from sg_mcproperty.server.app import build_server, mcp
from sg_mcproperty.server.private_residential import PrivateResidentialService


class StubRepository:
    def find_projects(self, query, tokens, district, limit):
        return [
            {
                "project": "AUREA",
                "street": "BEACH ROAD",
                "district": 7,
                "tenure": "99 years",
                "lease_year": 2024,
                "transactions": 75,
                "first_sale": datetime(2025, 3, 1),
                "last_sale": datetime(2026, 11, 1),
                "match_type": "exact",
            }
        ]

    def coverage(self):
        return {
            "transactions": 5,
            "projects": 2,
            "first_month": datetime(2025, 1, 1),
            "last_month": datetime(2026, 11, 1),
        }


def _run(coro):
    return asyncio.run(coro)


def test_default_server_builds():
    assert mcp.name == "sg-property"


def test_tools_prompts_and_resources_are_registered():
    server = build_server(PrivateResidentialService(StubRepository()))

    async def inspect():
        async with Client(server) as client:
            tools = {t.name for t in await client.list_tools()}
            prompts = {p.name for p in await client.list_prompts()}
            resources = {str(r.uri) for r in await client.list_resources()}
            return tools, prompts, resources

    tools, prompts, resources = _run(inspect())
    assert tools == {
        "search_projects",
        "search_transactions",
        "price_trend",
        "compare_nearby",
        "rank_nearby_growth",
        "affordability_search",
        "dataset_info",
        "project_summary",
        "market_activity",
        "estimate_value",
    }
    assert prompts == {"project_overview", "recent_deals", "value_project"}
    assert resources == {
        "property://private-residential/schema",
        "property://private-residential/coverage",
    }


def test_elicitation_tools_publish_only_their_result_schema():
    server = build_server(PrivateResidentialService(StubRepository()))

    async def inspect():
        async with Client(server) as client:
            return {tool.name: tool.output_schema for tool in await client.list_tools()}

    schemas = _run(inspect())
    for name in ("estimate_value", "compare_nearby", "rank_nearby_growth", "affordability_search"):
        properties = schemas[name].get("properties", {})
        assert "result_type" not in properties and "input_requests" not in properties


def test_tool_prompt_and_resource_end_to_end_with_stub():
    server = build_server(PrivateResidentialService(StubRepository()))

    async def exercise():
        async with Client(server) as client:
            tool = await client.call_tool("search_projects", {"name_query": "aurea"})
            prompt = await client.get_prompt("project_overview", {"project_name": "aurea"})
            schema = await client.read_resource("property://private-residential/schema")
            coverage = await client.read_resource("property://private-residential/coverage")
            return tool.structured_content, prompt, schema, coverage

    tool_data, prompt, schema, coverage = _run(exercise())
    project = tool_data["result"][0]
    assert project["project"] == "AUREA" and project["first_sale"] == "2025-03-01"
    assert "search_projects" in prompt.messages[0].content.text
    assert "psf" in schema[0].text
    assert json.loads(coverage[0].text)["last_month"] == "2026-11-01"
