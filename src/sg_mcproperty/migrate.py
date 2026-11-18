"""Minimal versioned-migration runner for ClickHouse.

Applies `clickhouse/migrations/NNNN_name.sql` files in order, once each, and records
them (with a checksum) in `<database>.schema_migrations`. Applied files must never be
edited; add a new migration instead.
"""

import hashlib
import sys
from pathlib import Path

from .config import Settings
from .db import get_client

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "clickhouse" / "migrations"


def _statements(sql: str) -> list[str]:
    """Split a file into statements (ClickHouse accepts one statement per call)."""
    lines = (ln for ln in sql.splitlines() if not ln.strip().startswith("--"))
    return [s.strip() for s in "\n".join(lines).split(";") if s.strip()]


def migrate(settings: Settings | None = None, directory: Path = MIGRATIONS_DIR) -> list[str]:
    settings = settings or Settings.from_env()
    db = settings.database
    # Connect to `default` first: the target database may not exist yet.
    client = get_client(settings, database="default")
    client.command(f"CREATE DATABASE IF NOT EXISTS {db}")
    client.command(
        f"""CREATE TABLE IF NOT EXISTS {db}.schema_migrations
            (version String, checksum String, applied_at DateTime DEFAULT now())
            ENGINE = MergeTree ORDER BY version"""
    )
    applied = {
        row[0]: row[1]
        for row in client.query(f"SELECT version, checksum FROM {db}.schema_migrations").result_rows
    }

    ran = []
    for path in sorted(directory.glob("*.sql")):
        sql = path.read_text()
        checksum = hashlib.sha256(sql.encode()).hexdigest()
        if path.stem in applied:
            if applied[path.stem] != checksum:
                raise RuntimeError(f"{path.name} was modified after being applied")
            continue
        for stmt in _statements(sql):
            client.command(stmt)
        client.insert(
            f"{db}.schema_migrations", [[path.stem, checksum]], column_names=["version", "checksum"]
        )
        ran.append(path.stem)
    return ran


def main() -> None:
    ran = migrate()
    print("Applied: " + ", ".join(ran) if ran else "Already up to date.", file=sys.stdout)


if __name__ == "__main__":
    main()
