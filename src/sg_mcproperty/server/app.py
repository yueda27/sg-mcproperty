"""FastMCP server entry point. Each module in `tools/` exposes `register(mcp)`."""

from fastmcp import FastMCP

from sg_mcproperty.server.tools import private_residential

mcp = FastMCP(
    "sg-property",
    instructions=(
        "Singapore property transaction data (HDB resale, private) in ClickHouse. "
        "Prices are in SGD. Use the tools rather than guessing figures."
    ),
)

private_residential.register(mcp)


def main() -> None:
    mcp.run()  # stdio by default


if __name__ == "__main__":
    main()
