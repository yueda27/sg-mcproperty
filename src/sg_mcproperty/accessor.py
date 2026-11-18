"""Thin accessor over ClickHouse for running parameterised queries."""

import re
from typing import Any

from clickhouse_connect.driver.exceptions import ClickHouseError

from .config import Settings
from .db import get_client

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class ClickHouseAccessor:
    """Runs queries against the configured database.

    Read-only by default (the server rejects writes and DDL). A fresh client is opened per
    call: clickhouse-connect clients share a session and are not safe for concurrent queries,
    and MCP tool calls can run concurrently.

    Values must go through ClickHouse `{name:Type}` parameters. Only trusted identifiers
    (table names) may be substituted into SQL text, via `table()`.
    """

    def __init__(self, settings: Settings | None = None, *, readonly: bool = True) -> None:
        self._settings = settings or Settings.from_env()
        self._readonly = readonly

    @property
    def database(self) -> str:
        return self._settings.database

    def table(self, name: str) -> str:
        """Fully qualified, validated table name, e.g. `sg_property.my_table`."""
        for part in (self.database, name):
            if not _IDENTIFIER.match(part):
                raise ValueError(f"invalid identifier: {part!r}")
        return f"{self.database}.{name}"

    def query(self, sql: str, parameters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Run a SELECT and return rows as dicts keyed by column name."""
        client = get_client(self._settings, readonly=self._readonly)
        try:
            result = client.query(sql, parameters=parameters)
        finally:
            client.close()
        return [dict(zip(result.column_names, row)) for row in result.result_rows]

    def ping(self) -> bool:
        """True if ClickHouse answers a trivial query."""
        try:
            self.query("SELECT 1")
        except (ClickHouseError, OSError):
            return False
        return True
