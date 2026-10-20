"""Inspect local networking and print GitHub input candidates without saving files.

IP configuration does not reveal switch VLAN membership. The report says so.
"""

from __future__ import annotations

import json
import argparse
import ipaddress
import subprocess
from pathlib import Path
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


def select_interface(report: dict[str, Any], name: str | None = None) -> dict[str, Any] | None:
    usable = [item for item in report["interfaces"] if item["operstate"] == "UP" and item["addresses"]]
    if name:
        matches = [item for item in usable if item["name"] == name]
        if not matches:
            raise ValueError(f"interface {name!r} is not up with an IPv4 address")
        return matches[0]
    defaults = {item["device"] for item in report["routes"]
                if item["destination"] == "default" and item["gateway"]}
    matches = [item for item in usable if item["name"] in defaults]
    return matches[0] if len(matches) == 1 else None


def usable_ipv4(values: list[str]) -> list[str]:
    result = []
    for value in values:
        try:
            address = ipaddress.IPv4Address(value)
        except ipaddress.AddressValueError:
            continue
        if address.is_loopback or address.is_unspecified or address.is_multicast or str(address) == "255.255.255.255":
            continue
        if str(address) not in result:
            result.append(str(address))
    return result


def summary(report: dict[str, Any], interface_name: str | None = None) -> str:
    lines = [
        "Network discovery (console only)",
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
    selected = select_interface(report, interface_name)
    gateway = "<CONFIRM_ROUTER_IPV4>"
    dns = "<CONFIRM_IPV4_DNS_JSON_LIST>"
    prefix = "<PREFIX_LENGTH>"
    if selected:
        lines.extend(["", "Local details (this computer, not the MAAS server identity):",
                      f"LOCAL_INTERFACE={selected['name']}",
                      f"LOCAL_ADDRESS={','.join(selected['addresses'])}",
                      f"LOCAL_MAC={selected['mac'] or 'unknown'}"])
        prefixes = {str(ipaddress.IPv4Interface(address).network.prefixlen) for address in selected["addresses"]}
        if len(prefixes) == 1:
            prefix = prefixes.pop()
        gateways = usable_ipv4([item["gateway"] for item in report["routes"]
                                if item["destination"] == "default" and item["device"] == selected["name"]
                                and item["gateway"]])
        if len(gateways) == 1:
            gateway = gateways[0]
        resolvers = usable_ipv4(report.get("dns", {}).get(selected["name"], []))
        if resolvers:
            dns = json.dumps(resolvers, separators=(",", ":"))
    else:
        lines.extend(["", "No single management interface selected. Rerun with --interface NAME."])
    lines.extend(
        [
            "",
            "GitHub Actions Secrets (confirm detected gateway/DNS in the router UI):",
            "MANAGEMENT_INTERFACE_MAC=<MAAS_SERVER_ETHERNET_MAC>",
            f"MANAGEMENT_ADDRESS=<UNUSED_MAAS_SERVER_IPV4>/{prefix}",
            f"MANAGEMENT_GATEWAY={gateway}",
            f"MANAGEMENT_DNS={dns}",
            "MANAGEMENT_VLAN_ID=<MANAGEMENT_VLAN_FROM_SWITCH_UI>",
            "",
            "Replace <...> using the MAAS server identity, a chosen unused address, and the switch UI.",
            "Do not use this computer's address or MAC as the MAAS identity unless it is that server.",
            "The router UI provides the LAN subnet, DHCP scope, gateway, DNS, and switch DHCP lease.",
            "The switch UI provides VLAN membership, management VLAN, and PVIDs.",
            "These network inputs are Secrets, not Variables. Nothing is saved or uploaded.",
        ]
    )
    return "\n".join(lines) + "\n"


def _dns_for_interface(name: str) -> list[str]:
    try:
        result = subprocess.run(["resolvectl", "dns", name], capture_output=True, text=True, check=False)
    except FileNotFoundError:
        result = None
    if result is not None and result.returncode == 0:
        # Each line begins with "Link N (name): "; IPv6 resolvers are ignored.
        values = [value for line in result.stdout.splitlines()
                  for value in line.partition(": ")[2].split()]
        return usable_ipv4(values)
    try:
        lines = Path("/etc/resolv.conf").read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return usable_ipv4([parts[1] for line in lines if len(parts := line.split()) >= 2 and parts[0] == "nameserver"])


def main() -> int:
    parser = argparse.ArgumentParser(description="Print local network details and GitHub Secret candidates; write no files.")
    parser.add_argument("--interface", help="management LAN interface when several default routes exist")
    args = parser.parse_args()
    try:
        snapshots = [json.loads(subprocess.run(command, capture_output=True, text=True, check=True).stdout)
                     for command in (["ip", "-j", "address"], ["ip", "-j", "link"], ["ip", "-j", "-4", "route"])]
        report = assemble(*snapshots)
        selected = select_interface(report, args.interface)
        if selected:
            report["dns"] = {selected["name"]: _dns_for_interface(selected["name"])}
        print(summary(report, args.interface), end="")
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Network discovery failed: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
