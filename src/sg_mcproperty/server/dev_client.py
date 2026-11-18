"""Development client for poking the MCP server's tools from a terminal or debugger.

    python -m sg_mcproperty.server.dev_client list
    python -m sg_mcproperty.server.dev_client call search_projects name_query=parktown district=18
    python -m sg_mcproperty.server.dev_client call search_projects '{"name_query": "parktown"}'
    python -m sg_mcproperty.server.dev_client read property://private-residential/coverage

Transports:
    memory (default): the server runs inside this process, so breakpoints in tool code are hit
        when you launch this file under the debugger.
    stdio: starts `python -m sg_mcproperty.server.app` as a subprocess and talks MCP to it,
        like a real client (Claude Desktop, Cursor) would.

ClickHouse settings come from the environment (see .env.example).
"""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from fastmcp import Client
from fastmcp.client.transports import StdioTransport


def parse_arguments(items: list[str]) -> dict[str, Any]:
    """Turn CLI arguments into tool arguments.

    Accepts one JSON object, or any number of key=value pairs where each value is parsed as
    JSON when possible (`district=18` is an int, `name_query=parktown` stays a string).
    """
    if len(items) == 1 and items[0].lstrip().startswith("{"):
        parsed = json.loads(items[0])
        if not isinstance(parsed, dict):
            raise ValueError("JSON arguments must be an object")
        return parsed

    arguments: dict[str, Any] = {}
    for item in items:
        key, sep, raw = item.partition("=")
        if not sep or not key:
            raise ValueError(f"expected key=value or a JSON object, got {item!r}")
        try:
            arguments[key] = json.loads(raw)
        except json.JSONDecodeError:
            arguments[key] = raw
    return arguments


def make_client(transport: str, server_logs: bool = False) -> Client:
    if transport == "stdio":
        # Pass the environment explicitly: stdio subprocesses only get a minimal one by default.
        return Client(
            StdioTransport(
                command=sys.executable,
                args=["-m", "sg_mcproperty.server.app"],
                env=dict(os.environ),
                # the subprocess prints a startup banner and logs: hide them unless asked
                log_file=None if server_logs else Path(os.devnull),
            )
        )
    from sg_mcproperty.server.app import mcp

    return Client(mcp)


async def list_tools(client: Client) -> None:
    for tool in await client.list_tools():
        print(f"\n== {tool.name}")
        print((tool.description or "").strip())
        print("input schema:", json.dumps(tool.input_schema, indent=2))


async def call_tool(client: Client, name: str, arguments: dict[str, Any]) -> None:
    result = await client.call_tool(name, arguments)
    # structured_content is the plain JSON the server returned (lists are wrapped in {"result": ...})
    data = result.structured_content if result.structured_content is not None else result.content
    print(json.dumps(data, indent=2, default=str))


async def read_resource(client: Client, uri: str) -> None:
    for content in await client.read_resource(uri):
        text = getattr(content, "text", None)
        if text is None:
            print(content)
            continue
        try:  # pretty print JSON resources, show anything else (markdown) as is
            print(json.dumps(json.loads(text), indent=2))
        except json.JSONDecodeError:
            print(text)


async def run(args: argparse.Namespace) -> None:
    async with make_client(args.transport, args.server_logs) as client:
        if args.command == "list":
            await list_tools(client)
        elif args.command == "read":
            await read_resource(client, args.uri)
        else:
            await call_tool(client, args.tool, parse_arguments(args.arguments))


def main() -> None:
    parser = argparse.ArgumentParser(prog="dev_client", description=__doc__.split("\n")[0])
    parser.add_argument("--transport", choices=["memory", "stdio"], default="memory")
    parser.add_argument(
        "--server-logs", action="store_true", help="show the stdio server's banner and logs"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="list tools with descriptions and input schemas")
    read = sub.add_parser("read", help="read one resource by URI")
    read.add_argument("uri", help="e.g. property://private-residential/coverage")
    call = sub.add_parser("call", help="call one tool")
    call.add_argument("tool")
    call.add_argument("arguments", nargs="*", help="key=value pairs, or one JSON object")
    args = parser.parse_args()

    try:
        asyncio.run(run(args))
    except Exception as exc:  # noqa: BLE001  dev tool: show the error, not async stack frames
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
