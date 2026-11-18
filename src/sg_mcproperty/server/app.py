"""FastMCP server entry point and composition root.

Wiring, bottom to top: accessor -> repository -> service -> MCP tools, prompts and resources.
Each of `tools/`, `prompts/` and `resources/` exposes `register(mcp, ...)` per dataset.
"""

from fastmcp import FastMCP

from sg_mcproperty.accessor import ClickHouseAccessor
from sg_mcproperty.server.private_rental import PrivateRentalRepository
from sg_mcproperty.server.private_residential import (
    PrivateResidentialRepository,
    PrivateResidentialService,
)
from sg_mcproperty.server.prompts import private_residential as private_residential_prompts
from sg_mcproperty.server.resources import private_residential as private_residential_resources
from sg_mcproperty.server.tools import private_residential as private_residential_tools


def build_server(service: PrivateResidentialService | None = None) -> FastMCP:
    """Build the MCP server. Pass a service to use something other than the live database."""
    service = service or PrivateResidentialService(
        PrivateResidentialRepository(ClickHouseAccessor()),
        PrivateRentalRepository(ClickHouseAccessor()),
    )
    mcp = FastMCP(
        "sg-property",
        instructions=(
            "Singapore private residential property transactions (last 5 years) in ClickHouse. "
            "Prices are in SGD and areas in sqft. Use the tools rather than guessing figures, "
            "and read the private-residential schema resource for column meanings."
        ),
    )
    private_residential_tools.register(mcp, service)
    private_residential_prompts.register(mcp)
    private_residential_resources.register(mcp, service)
    return mcp


mcp = build_server()


def main() -> None:
    mcp.run()  # stdio by default


if __name__ == "__main__":
    main()
