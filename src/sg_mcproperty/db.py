"""ClickHouse client factory shared by the migrator, loaders and MCP server."""

import clickhouse_connect
from clickhouse_connect.driver import Client

from .config import Settings


def get_client(
    settings: Settings | None = None,
    *,
    database: str | None = None,
    readonly: bool = False,
) -> Client:
    """Create a client. `readonly=True` makes the server reject any write/DDL."""
    s = settings or Settings.from_env()
    return clickhouse_connect.get_client(
        host=s.host,
        port=s.port,
        username=s.user,
        password=s.password,
        database=database if database is not None else s.database,
        settings={"readonly": 1} if readonly else {},
    )
