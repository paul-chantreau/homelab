"""Plan MAAS subnet and VLAN API calls.

The planner never enables DHCP. Activation is a separate, explicit operation
and it only targets the provisioning VLAN recorded in the configuration.
"""

from __future__ import annotations

import ipaddress
from typing import Any


class PlanError(Exception):
    """The MAAS inventory cannot be reconciled with the configuration."""


def _subnet_by_cidr(inventory: dict[str, Any], cidr: str) -> dict[str, Any] | None:
    target = ipaddress.ip_network(cidr, strict=True)
    for subnet in inventory.get("subnets") or []:
        if ipaddress.ip_network(str(subnet.get("cidr")), strict=False) == target:
            return subnet
    return None


def controller_vlan(network: dict[str, Any], inventory: dict[str, Any]) -> dict[str, Any]:
    """Anchor the provisioning VLAN to the sole rack's verified NIC/interface."""
    racks = inventory.get("racks") or []
    if len(racks) != 1 or not racks[0].get("system_id"):
        raise PlanError("exactly one MAAS rack controller must be registered")
    mac = str(network["management"]["interface_mac"]).lower()
    interfaces = inventory.get("interfaces") or []
    physical = [item for item in interfaces if item.get("type") == "physical" and str(item.get("mac_address", "")).lower() == mac]
    if len(physical) != 1:
        raise PlanError("rack must report exactly one physical interface with the configured MAC")
    candidates = [item for item in interfaces if item.get("name") == "provisioning" and item.get("type") == "vlan"
                  and str(item.get("mac_address", "")).lower() == mac
                  and (item.get("vlan") or {}).get("vid") == int(network["provisioning"]["vlan_id"])
                  and physical[0]["name"] in {str(parent) for parent in item.get("parents") or []}]
    if len(candidates) != 1:
        raise PlanError("rack must report the provisioning VLAN on the configured physical interface")
    vlan_id = candidates[0]["vlan"]["id"]
    matches = [item for item in inventory.get("vlans") or [] if item.get("id") == vlan_id]
    if len(matches) != 1:
        raise PlanError("provisioning interface VLAN is missing or ambiguous in the fabric inventory")
    return matches[0]


def subnet_vlan(subnet: dict[str, Any], inventory: dict[str, Any]) -> dict[str, Any]:
    value = subnet.get("vlan") or {}
    matches = [item for item in inventory.get("vlans") or [] if item.get("id") == value.get("id")]
    if len(matches) != 1:
        raise PlanError("subnet VLAN cannot be resolved unambiguously")
    return matches[0]


def _ranges_for(inventory: dict[str, Any], subnet_id: Any) -> list[dict[str, Any]]:
    return [
        item
        for item in inventory.get("ipranges") or []
        if str(item["subnet"].get("id") if isinstance(item.get("subnet"), dict) else item.get("subnet", item.get("subnet_id"))) == str(subnet_id)
        or str(item.get("subnet")) == str(subnet_id)
    ]


def plan_initial(network: dict[str, Any], inventory: dict[str, Any]) -> list[dict[str, Any]]:
    """Create the provisioning subnet and ranges while leaving DHCP disabled.

    A later rerun does not turn provisioning DHCP off if an administrator has
    already activated it. DHCP on the management VLAN is always forced off.
    """
    actions: list[dict[str, Any]] = []
    provisioning = network["provisioning"]
    vlan = controller_vlan(network, inventory)
    cidr = str(provisioning["subnet"])
    subnet = _subnet_by_cidr(inventory, cidr)
    if subnet is None:
        actions.append(
            {
                "op": "subnet-create",
                "args": {"cidr": cidr, "name": "provisioning", "vlan": vlan["id"], "managed": "true"},
            }
        )
        subnet_id = "$provisioning"
    else:
        if subnet_vlan(subnet, inventory)["id"] != vlan["id"]:
            raise PlanError("provisioning subnet is attached to a different VLAN; refusing to move it")
        subnet_id = subnet["id"]
        actions.append(
            {
                "op": "subnet-update",
                "id": subnet_id,
                "args": {"name": "provisioning"},
            }
        )

    # Keep provisioning isolated, including clearing stale options on a rerun.
    actions.append(
        {
            "op": "subnet-update",
            "id": subnet_id,
            "args": {
                "gateway_ip": "",
                "dns_servers": "",
                "managed": "true",
            },
        }
    )
    controller = str(provisioning["controller_address"])
    actions.extend(_ensure_range(inventory, subnet_id, "reserved", controller, controller, "maas-controller"))
    dhcp = provisioning["dhcp"]
    actions.extend(
        _ensure_range(
            inventory,
            subnet_id,
            "dynamic",
            str(dhcp["range_start"]),
            str(dhcp["range_end"]),
            "provisioning-dynamic",
        )
    )

    if vlan.get("dhcp_on") is not True:
        actions.append(
            {
                "op": "vlan-update",
                "fabric_id": vlan["fabric_id"],
                "vid": int(provisioning["vlan_id"]),
                "args": {"dhcp_on": "false"},
            }
        )

    management = ipaddress.ip_interface(str(network["management"]["address"])).network
    management_subnet = _subnet_by_cidr(inventory, str(management))
    if management_subnet is not None:
        management_vlan = subnet_vlan(management_subnet, inventory)
        if management_vlan["id"] == vlan["id"]:
            raise PlanError("management and provisioning subnets share a MAAS VLAN")
        if management_vlan.get("dhcp_on") is True:
            actions.append(
                {
                    "op": "vlan-update",
                    "fabric_id": management_vlan["fabric_id"],
                    "vid": int(management_vlan["vid"]),
                    "args": {"dhcp_on": "false"},
                }
            )
    return actions


def plan_enable(network: dict[str, Any], inventory: dict[str, Any]) -> list[dict[str, Any]]:
    provisioning = network["provisioning"]
    vid = int(provisioning["vlan_id"])
    vlan = controller_vlan(network, inventory)
    racks = inventory.get("racks") or []
    if len(racks) != 1 or not racks[0].get("system_id"):
        raise PlanError("exactly one MAAS rack controller must be registered")
    subnet = _subnet_by_cidr(inventory, str(provisioning["subnet"]))
    if subnet is None:
        raise PlanError("the provisioning subnet does not exist in MAAS")
    if subnet_vlan(subnet, inventory)["id"] != vlan["id"] or subnet.get("managed") is not True:
        raise PlanError("provisioning subnet must be managed and attached to the controller VLAN")
    ranges = _ranges_for(inventory, subnet["id"])
    dhcp = provisioning["dhcp"]
    if not any(item.get("type") == "dynamic" and item.get("start_ip") == dhcp["range_start"]
               and item.get("end_ip") == dhcp["range_end"] for item in ranges):
        raise PlanError("configured dynamic range is not present on the provisioning subnet")
    management_vid = int(network["management"]["vlan_id"])
    if vid == management_vid:
        raise PlanError("refusing to enable DHCP on the management VLAN")
    return [
        {
            "op": "vlan-update",
            "fabric_id": vlan["fabric_id"],
            "vid": vid,
            "args": {"dhcp_on": "true", "primary_rack": racks[0]["system_id"]},
        }
    ]


def plan_disable(network: dict[str, Any], inventory: dict[str, Any]) -> list[dict[str, Any]]:
    vid = int(network["provisioning"]["vlan_id"])
    vlan = controller_vlan(network, inventory)
    return [
        {
            "op": "vlan-update",
            "fabric_id": vlan["fabric_id"],
            "vid": vid,
            "args": {"dhcp_on": "false"},
        }
    ]


def _ensure_range(
    inventory: dict[str, Any],
    subnet_id: Any,
    range_type: str,
    start: str,
    end: str,
    comment: str,
) -> list[dict[str, Any]]:
    if isinstance(subnet_id, str) and subnet_id.startswith("$"):
        return [
            {
                "op": "iprange-create",
                "args": {
                    "type": range_type,
                    "start_ip": start,
                    "end_ip": end,
                    "subnet": subnet_id,
                    "comment": comment,
                },
            }
        ]
    for existing in _ranges_for(inventory, subnet_id):
        if (
            existing.get("type") == range_type
            and existing.get("start_ip") == start
            and existing.get("end_ip") == end
        ):
            return []
    return [
        {
            "op": "iprange-create",
            "args": {
                "type": range_type,
                "start_ip": start,
                "end_ip": end,
                "subnet": str(subnet_id),
                "comment": comment,
            },
        }
    ]


def render_maas_command(profile: str, action: dict[str, Any]) -> list[str]:
    """Render one planner action as a MAAS CLI argument vector."""
    operation = action["op"]
    if operation == "subnet-create":
        command = ["maas", profile, "subnets", "create"]
    elif operation == "subnet-update":
        command = ["maas", profile, "subnet", "update", str(action["id"])]
    elif operation == "iprange-create":
        command = ["maas", profile, "ipranges", "create"]
    elif operation == "vlan-update":
        command = [
            "maas",
            profile,
            "vlan",
            "update",
            str(action["fabric_id"]),
            str(action["vid"]),
        ]
    else:
        raise PlanError(f"unknown action {operation}")
    for key, value in action.get("args", {}).items():
        command.append(f"{key}={value}")
    return command
