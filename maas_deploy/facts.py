"""Convert command output into the fact snapshot used by acceptance checks."""

from __future__ import annotations

import json
from typing import Any


def load_json(text: str) -> Any:
    data = json.loads(text)
    return data if isinstance(data, list) else []


def interfaces_from_ip(address_text: str, link_text: str) -> list[dict[str, Any]]:
    addresses = {item.get("ifname"): item for item in load_json(address_text)}
    interfaces = []
    for link in load_json(link_text):
        name = link.get("ifname")
        address = addresses.get(name) or {}
        info = ((link.get("linkinfo") or {}).get("info_data") or {})
        kind = (link.get("linkinfo") or {}).get("info_kind")
        ipv4 = []
        for addr in address.get("addr_info") or []:
            if addr.get("family") == "inet" and addr.get("local") is not None:
                ipv4.append(f"{addr['local']}/{addr.get('prefixlen')}")
        interfaces.append(
            {
                "name": name,
                "mac": str(link.get("address") or "").lower(),
                "operstate": link.get("operstate") or address.get("operstate"),
                "addresses": ipv4,
                "vlan_id": info.get("id") if kind == "vlan" else None,
            }
        )
    return interfaces


def default_route(route_text: str) -> dict[str, Any]:
    for route in load_json(route_text):
        if route.get("dst") in {None, "default"}:
            return {"via": route.get("gateway"), "device": route.get("dev")}
    return {}


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
