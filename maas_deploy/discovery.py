"""Assemble a read-only network discovery report.

IP configuration does not reveal switch VLAN membership. The report says so.
"""

from __future__ import annotations

import json
from typing import Any


def assemble(
    address: Any,
    link: Any,
    route: Any,
    networkctl: str = "",
    resolvectl: str = "",
) -> dict[str, Any]:
    interfaces = []
    link_by_index = {}
    if isinstance(link, list):
        for item in link:
            link_by_index[item.get("ifindex")] = item
    if isinstance(address, list):
        for item in address:
            matched = link_by_index.get(item.get("ifindex"), {})
            addresses = []
            for addr in item.get("addr_info") or []:
                local = addr.get("local")
                prefix = addr.get("prefixlen")
                if local and prefix is not None and addr.get("family") == "inet":
                    addresses.append(f"{local}/{prefix}")
            interfaces.append(
                {
                    "name": item.get("ifname"),
                    "mac": (matched.get("address") or item.get("address") or "").lower(),
                    "operstate": matched.get("operstate") or item.get("operstate"),
                    "addresses": addresses,
                }
            )
    routes = []
    if isinstance(route, list):
        for item in route:
            if item.get("dst") in {None, "default"} or item.get("gateway"):
                routes.append(
                    {
                        "destination": item.get("dst") or "default",
                        "gateway": item.get("gateway"),
                        "device": item.get("dev"),
                    }
                )
    return {
        "source": "read-only-linux-commands",
        "limitation": (
            "This report is local IP configuration only. It does not identify Netgear "
            "VLAN membership, PVID values, or which physical switch port a cable uses."
        ),
        "interfaces": interfaces,
        "routes": routes,
        "networkctl": networkctl.strip(),
        "resolvectl": resolvectl.strip(),
    }


def summary(report: dict[str, Any]) -> str:
    lines = [
        "Network discovery summary",
        report["limitation"],
        "",
        "Interfaces:",
    ]
    for interface in report["interfaces"]:
        addresses = ", ".join(interface["addresses"]) or "no IPv4 address"
        lines.append(
            f"  {interface['name']}  mac={interface['mac'] or 'unknown'}  "
            f"state={interface['operstate']}  {addresses}"
        )
    lines.append("")
    lines.append("Routes:")
    for route in report["routes"]:
        lines.append(
            f"  {route['destination']} via {route['gateway'] or 'on-link'} dev {route['device']}"
        )
    if not report["routes"]:
        lines.append("  none observed")
    lines.extend(
        [
            "",
            "Still collect these manually:",
            "  router address and existing DHCP scope",
            "  Netgear GS724Tv4 management IP",
            "  physical switch port for the router, the server, and each machine",
            "",
            json.dumps({"interfaces": len(report["interfaces"]), "routes": len(report["routes"])}),
        ]
    )
    return "\n".join(lines) + "\n"
