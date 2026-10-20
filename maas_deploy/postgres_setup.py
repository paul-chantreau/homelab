"""Idempotent production PostgreSQL preparation for MAAS.

The database and role are created only when they are absent. A rerun does not
drop the database or rotate the password already stored on the server.
"""

from __future__ import annotations

from typing import Any


def hba_block(database: str, user: str) -> str:
    return (
        "# BEGIN maas-provision\n"
        f"local   {database}   {user}   scram-sha-256\n"
        f"host    {database}   {user}   127.0.0.1/32   scram-sha-256\n"
        f"host    {database}   {user}   ::1/128   scram-sha-256\n"
        "# END maas-provision\n"
    )


def merge_hba(existing: str, database: str, user: str) -> str:
    block = hba_block(database, user)
    start = "# BEGIN maas-provision\n"
    end = "# END maas-provision\n"
    if start in existing and end in existing:
        before, rest = existing.split(start, 1)
        _, after = rest.split(end, 1)
        return before + block + after
    return block + existing


def role_sql(user: str, password: str, role_exists: bool) -> str | None:
    if role_exists:
        return None
    escaped = password.replace("'", "''")
    return f"CREATE ROLE \"{user}\" LOGIN PASSWORD '{escaped}';"


def database_sql(database: str, user: str, database_exists: bool) -> str | None:
    if database_exists:
        return None
    return f'CREATE DATABASE "{database}" OWNER "{user}";'


def plan(state: dict[str, Any], database: str, user: str, password: str) -> dict[str, Any]:
    """Return SQL and pg_hba text without applying them."""
    return {
        "role_sql": role_sql(user, password, bool(state.get("role_exists"))),
        "database_sql": database_sql(database, user, bool(state.get("database_exists"))),
        "hba": merge_hba(str(state.get("hba", "")), database, user),
        "changes": not state.get("role_exists") or not state.get("database_exists"),
    }
