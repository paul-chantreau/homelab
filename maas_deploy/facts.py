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
    region = False
    rack = False
    dhcp = False
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        name = parts[0].lower().rstrip(":")
        state = parts[-1].lower()
        running = state in {"running", "active"}
        if name.startswith("regiond") and running:
            region = True
        if name == "rackd" and running:
            rack = True
        if name in {"dhcpd", "dhcpd4"} and running:
            dhcp = True
    return {
        "present": bool(text.strip()),
        "region_active": region,
        "rack_active": rack,
        "dhcp_active": dhcp,
    }


def dhcp_offer_count(capture: str) -> int:
    count = 0
    for line in capture.splitlines():
        lowered = line.lower()
        if "offer" in lowered or "message-type offer" in lowered:
            count += 1
    return count
