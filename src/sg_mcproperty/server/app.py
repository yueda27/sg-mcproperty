"""FastMCP server entry point. Each module in `tools/` exposes `register(mcp)`."""

from fastmcp import FastMCP


mcp = FastMCP(
    "sg-property",
    instructions=(
        "Singapore property transaction data (HDB resale, private) in ClickHouse. "
        "Prices are in SGD. Use the tools rather than guessing figures."
    ),
)


def main() -> None:
    mcp.run()  # stdio by default


if __name__ == "__main__":
    main()
