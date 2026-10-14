"""Create local credentials once and reuse them on later runs."""

from __future__ import annotations

import os
import secrets
import stat
from pathlib import Path

KEYS = ("POSTGRES_PASSWORD", "MAAS_ADMIN_PASSWORD")


def ensure(path: Path) -> str:
    """Create a mode 0600 secrets file if needed. Never prints secret values."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        _tighten(path)
        load(path)
        return "exists"
    values = {key: secrets.token_urlsafe(32) for key in KEYS}
    _write(path, values)
    return "created"


def load(path: Path) -> dict[str, str]:
    if not path.exists():
        raise FileNotFoundError(path)
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        raise PermissionError(f"{path} is readable by a group or by others")
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value
    missing = [key for key in KEYS if not values.get(key)]
    if missing:
        raise ValueError("secrets file is missing " + ", ".join(missing))
    return values


def store_api_key(path: Path, api_key: str) -> None:
    values = load(path)
    values["MAAS_API_KEY"] = api_key.strip()
    _write(path, values)


def _write(path: Path, values: dict[str, str]) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            for key in ("POSTGRES_PASSWORD", "MAAS_ADMIN_PASSWORD", "MAAS_API_KEY"):
                if key in values:
                    handle.write(f"{key}={values[key]}\n")
    except Exception:
        os.close(fd)
        raise
    _tighten(path)


def _tighten(path: Path) -> None:
    os.chmod(path, 0o600)
