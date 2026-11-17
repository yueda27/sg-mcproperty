"""Connection settings, read from environment variables (see .env.example)."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    host: str = "localhost"
    port: int = 8123
    user: str = "default"
    password: str = ""
    database: str = "sg_property"

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            host=os.getenv("CLICKHOUSE_HOST", cls.host),
            port=int(os.getenv("CLICKHOUSE_PORT", cls.port)),
            user=os.getenv("CLICKHOUSE_USER", cls.user),
            password=os.getenv("CLICKHOUSE_PASSWORD", cls.password),
            database=os.getenv("CLICKHOUSE_DATABASE", cls.database),
        )
