"""Render Netplan for the single physical NIC and its provisioning VLAN."""

from __future__ import annotations

import ipaddress
from typing import Any

import yaml


def render_netplan(network: dict[str, Any]) -> str:
    """Build Netplan YAML from the canonical network configuration.

    The physical NIC is selected by MAC address. The default route stays on
    the untagged management LAN. The provisioning VLAN never receives a
    default route, and IP forwarding is not enabled here.
    """
    management = network["management"]
    provisioning = network["provisioning"]
    mac = str(management["interface_mac"]).lower()
    prefix = ipaddress.ip_network(str(provisioning["subnet"]), strict=True).prefixlen
    controller = f"{provisioning['controller_address']}/{prefix}"
    document: dict[str, Any] = {
        "network": {
            "version": 2,
            "renderer": "networkd",
            "ethernets": {
                "maas_nic": {
                    "match": {"macaddress": mac},
                    "dhcp4": False,
                    "dhcp6": False,
                    "accept-ra": False,
                    "addresses": [str(management["address"])],
                    "routes": [
                        {
                            "to": "default",
                            "via": str(management["gateway"]),
                        }
                    ],
                    "nameservers": {"addresses": [str(item) for item in management["dns"]]},
                }
            },
            "vlans": {
                "provisioning": {
                    "id": int(provisioning["vlan_id"]),
                    "link": "maas_nic",
                    "dhcp4": False,
                    "dhcp6": False,
                    "accept-ra": False,
                    "addresses": [controller],
                }
            },
        }
    }
    header = (
        "# Generated from config/network.yaml. Do not edit by hand.\n"
        "# The default route is the management LAN. The provisioning VLAN has no default route.\n"
    )
    return header + yaml.safe_dump(document, sort_keys=False)
