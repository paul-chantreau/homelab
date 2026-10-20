"""Parse MAAS service status for controller readiness and health checks."""

from __future__ import annotations

from typing import Any


def parse_maas_status(text: str) -> dict[str, Any]:
    """Parse Pebble's Current column, with explicit legacy-status support."""
    services = {}
    current_column = None
    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue
        if parts[0].lower() == "service" and "Current" in parts:
            current_column = parts.index("Current")
            continue
        if current_column is not None:
            if len(parts) <= current_column:
                continue
            state = parts[current_column].lower()
        elif len(parts) >= 2 and parts[1].lower() in {"running", "stopped", "active", "inactive"}:
            state = parts[1].lower()
        else:
            continue
        services[parts[0].lower().rstrip(":")] = state
    active = lambda names: any(services.get(name) in {"active", "running"} for name in names)
    return {
        "present": bool(services),
        "region_active": active([name for name in services if name.startswith("regiond")]),
        "rack_active": active(["rackd"]),
        "dhcp_active": active(["dhcpd", "dhcpd4"]),
        "services": services,
    }
