"""Local PostgreSQL dump retention for the MAAS database."""

from __future__ import annotations

from pathlib import Path


def select_expired(paths: list[Path], retain: int) -> list[Path]:
    """Return dumps that should be removed, oldest first, keeping the newest."""
    ordered = sorted(paths, key=lambda path: path.name)
    if retain < 1:
        raise ValueError("retain must be positive")
    if len(ordered) <= retain:
        return []
    return ordered[: len(ordered) - retain]


def dump_command(database: str, destination: Path) -> list[str]:
    return [
        "sudo",
        "-u",
        "postgres",
        "pg_dump",
        "--format=custom",
        "--file",
        str(destination),
        database,
    ]


def rsync_command(source: Path, target: str) -> list[str]:
    if not target or target.startswith("-") or " " in target:
        raise ValueError("off-host target is not a usable rsync destination")
    return ["rsync", "-a", "--", str(source), target]
