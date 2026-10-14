"""Decide VLAN acceptance from observed facts.

The GS724T itself is not observable from the server NIC. A passing VLAN
interface is not evidence that the switch is configured. Isolation requires a
capture taken on a second host that is on the management LAN.
"""

from __future__ import annotations

import ipaddress
from datetime import datetime, timezone
from typing import Any

PASS = "PASS"
FAIL = "FAIL"
INCONCLUSIVE = "INCONCLUSIVE"

CRITICAL_CHECKS = (
    "nic_present",
    "nic_mac",
    "link_up",
    "management_address",
    "management_gateway",
    "default_route",
    "vlan_interface",
    "vlan_id",
    "provisioning_address",
    "provisioning_subnet",
    "test_host_bidirectional",
    "vlan_isolation",
    "unexpected_dhcp",
    "dhcp_range",
    "maas_rack",
    "switch_confirmation",
)


def _result(status: str, detail: str) -> dict[str, str]:
    if status not in {PASS, FAIL, INCONCLUSIVE}:
        raise ValueError(status)
    return {"status": status, "detail": detail}


def evaluate(network: dict[str, Any], facts: dict[str, Any], config_hash: str) -> dict[str, Any]:
    """Return a validation report. Missing evidence is INCONCLUSIVE, never PASS."""
    checks: dict[str, dict[str, str]] = {}
    mac = str(network["management"]["interface_mac"]).lower()
    interfaces = facts.get("interfaces") or []
    physical = _find_mac(interfaces, mac)
    checks["nic_present"] = (
        _result(PASS, "physical NIC with the configured MAC is present")
        if physical
        else _result(FAIL, "no interface has the configured MAC address")
    )
    observed_mac = str((physical or {}).get("mac", "")).lower()
    checks["nic_mac"] = (
        _result(PASS, observed_mac)
        if physical and observed_mac == mac
        else _result(FAIL, f"expected {mac}, observed {observed_mac or 'none'}")
    )
    oper = str((physical or {}).get("operstate", "")).upper()
    checks["link_up"] = (
        _result(PASS, oper)
        if oper == "UP"
        else _result(FAIL, f"physical link state is {oper or 'unknown'}")
    )

    management_cidr = ipaddress.ip_interface(str(network["management"]["address"]))
    addresses = set((physical or {}).get("addresses") or [])
    checks["management_address"] = (
        _result(PASS, str(management_cidr))
        if str(management_cidr) in addresses
        else _result(FAIL, f"{management_cidr} is not configured on the physical NIC")
    )

    gateway = str(network["management"]["gateway"])
    gateway_fact = facts.get("management_gateway") or {}
    if gateway_fact.get("reachable") is True and gateway_fact.get("device") == (physical or {}).get("name"):
        checks["management_gateway"] = _result(PASS, f"{gateway} answered on {(physical or {}).get('name')}")
    elif gateway_fact.get("reachable") is False:
        checks["management_gateway"] = _result(FAIL, f"{gateway} did not answer ICMP")
    else:
        checks["management_gateway"] = _result(INCONCLUSIVE, "gateway reachability was not measured")

    default_route = facts.get("default_route") or {}
    vlan_name = "provisioning"
    if default_route.get("via") == gateway and default_route.get("device") == (physical or {}).get("name"):
        checks["default_route"] = _result(PASS, f"default route via {gateway} on the physical NIC")
    elif default_route.get("device") == vlan_name:
        checks["default_route"] = _result(FAIL, "the default route points at the provisioning VLAN")
    elif not default_route:
        checks["default_route"] = _result(FAIL, "no default route was observed")
    else:
        checks["default_route"] = _result(
            FAIL,
            f"default route via {default_route.get('via')} dev {default_route.get('device')}",
        )

    vlan = _find_name(interfaces, vlan_name)
    checks["vlan_interface"] = (
        _result(PASS, vlan_name)
        if vlan
        else _result(FAIL, "the provisioning VLAN interface does not exist")
    )
    expected_vlan = int(network["provisioning"]["vlan_id"])
    observed_vlan = (vlan or {}).get("vlan_id")
    checks["vlan_id"] = (
        _result(PASS, str(expected_vlan))
        if observed_vlan == expected_vlan
        else _result(FAIL, f"expected VLAN {expected_vlan}, observed {observed_vlan}")
    )
    prefix = ipaddress.ip_network(str(network["provisioning"]["subnet"]), strict=True).prefixlen
    controller = ipaddress.ip_interface(f"{network['provisioning']['controller_address']}/{prefix}")
    vlan_addresses = set((vlan or {}).get("addresses") or [])
    checks["provisioning_address"] = (
        _result(PASS, str(controller))
        if str(controller) in vlan_addresses
        else _result(FAIL, f"{controller} is not configured on the VLAN interface")
    )
    subnet = ipaddress.ip_network(str(network["provisioning"]["subnet"]), strict=True)
    prefix_ok = any(
        ipaddress.ip_interface(item).network == subnet for item in vlan_addresses if "/" in str(item)
    )
    checks["provisioning_subnet"] = (
        _result(PASS, str(subnet))
        if prefix_ok
        else _result(FAIL, f"VLAN interface is not numbered on {subnet}")
    )

    checks["test_host_bidirectional"] = _test_host(network, facts)
    checks["vlan_isolation"] = _isolation(network, facts)
    checks["unexpected_dhcp"] = _dhcp(facts)
    checks["dhcp_range"] = _dhcp_range(network)
    checks["maas_rack"] = _rack(facts)
    checks["switch_confirmation"] = _switch(facts, config_hash)

    for name in CRITICAL_CHECKS:
        checks.setdefault(name, _result(INCONCLUSIVE, "check did not run"))

    statuses = {item["status"] for item in checks.values()}
    if FAIL in statuses:
        outcome = FAIL
    elif INCONCLUSIVE in statuses:
        outcome = INCONCLUSIVE
    else:
        outcome = PASS
    return {
        "outcome": outcome,
        "config_hash": config_hash,
        "checks": checks,
        "blocks_dhcp": outcome != PASS,
    }


def _find_mac(interfaces: list[dict[str, Any]], mac: str) -> dict[str, Any] | None:
    for interface in interfaces:
        if str(interface.get("mac", "")).lower() == mac and not interface.get("vlan_id"):
            return interface
    return None


def _find_name(interfaces: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    for interface in interfaces:
        if interface.get("name") == name:
            return interface
    return None


def _test_host(network: dict[str, Any], facts: dict[str, Any]) -> dict[str, str]:
    expected = (network.get("provisioning", {}).get("acceptance") or {}).get("test_host")
    observed = facts.get("test_host") or {}
    if not expected:
        return _result(INCONCLUSIVE, "network.provisioning.acceptance.test_host is not set")
    if observed.get("address") != expected:
        return _result(INCONCLUSIVE, "the test host was not exercised")
    if observed.get("ping") is not True:
        return _result(FAIL, f"{expected} did not answer ping on the provisioning VLAN")
    if observed.get("arping") is not True:
        return _result(INCONCLUSIVE, f"{expected} answered ping, but ARP on the VLAN interface was not confirmed")
    if observed.get("reverse_tcp") is not True:
        return _result(
            INCONCLUSIVE,
            "the server can reach the test host, but the test host has not connected back",
        )
    return _result(PASS, f"bidirectional connectivity with {expected}")


def _isolation(network: dict[str, Any], facts: dict[str, Any]) -> dict[str, str]:
    report = facts.get("isolation_report")
    if not isinstance(report, dict):
        return _result(
            INCONCLUSIVE,
            "no management-LAN capture was supplied. A VLAN interface on this host is not isolation evidence",
        )
    if report.get("captured_on_maas_server") is True:
        return _result(FAIL, "the capture was taken on the MAAS trunk and cannot prove isolation")
    vlan_id = int(network["provisioning"]["vlan_id"])
    test_host = (network.get("provisioning", {}).get("acceptance") or {}).get("test_host")
    if report.get("vlan_id") != vlan_id:
        return _result(INCONCLUSIVE, "the capture was for a different VLAN")
    if test_host and report.get("observed_host") != test_host:
        return _result(INCONCLUSIVE, "the capture did not watch the acceptance test host")
    seconds = report.get("seconds")
    if not isinstance(seconds, int) or seconds < 20:
        return _result(INCONCLUSIVE, "the management-LAN capture was shorter than 20 seconds")
    if report.get("packet_count") == 0:
        return _result(PASS, "the management-LAN observer saw no provisioning VLAN traffic")
    if isinstance(report.get("packet_count"), int) and report["packet_count"] > 0:
        return _result(FAIL, "provisioning traffic was observed on the management LAN")
    return _result(INCONCLUSIVE, "the isolation report did not include a packet count")


def _dhcp(facts: dict[str, Any]) -> dict[str, str]:
    capture = facts.get("dhcp_capture") or {}
    if capture.get("ran") is not True:
        return _result(INCONCLUSIVE, "no DHCP capture was taken on the provisioning VLAN")
    offers = capture.get("offer_count")
    if offers == 0:
        return _result(PASS, "no DHCP offer was observed on the provisioning VLAN during the capture")
    if isinstance(offers, int) and offers > 0:
        return _result(FAIL, f"{offers} unexpected DHCP offer(s) were observed")
    return _result(INCONCLUSIVE, "the DHCP capture did not report an offer count")


def _dhcp_range(network: dict[str, Any]) -> dict[str, str]:
    dhcp = network["provisioning"]["dhcp"]
    subnet = ipaddress.ip_network(str(network["provisioning"]["subnet"]), strict=True)
    start = ipaddress.ip_address(str(dhcp["range_start"]))
    end = ipaddress.ip_address(str(dhcp["range_end"]))
    controller = ipaddress.ip_address(str(network["provisioning"]["controller_address"]))
    if start >= end or start not in subnet or end not in subnet or start <= controller <= end:
        return _result(FAIL, "the configured DHCP range is not safe")
    return _result(PASS, f"{start}-{end} inside {subnet}, excluding {controller}")


def _rack(facts: dict[str, Any]) -> dict[str, str]:
    rack = facts.get("maas_rack") or {}
    if rack.get("region_active") is True and rack.get("rack_active") is True and rack.get("dhcp_active") is False:
        return _result(PASS, "region and rack are active and DHCP is not active")
    if rack.get("present") is False:
        return _result(FAIL, "MAAS rack services are not installed")
    if rack.get("dhcp_active") is True:
        return _result(FAIL, "MAAS DHCP is already active before acceptance")
    return _result(INCONCLUSIVE, "MAAS region or rack readiness was not confirmed")


def _switch(facts: dict[str, Any], config_hash: str) -> dict[str, str]:
    confirmation = facts.get("switch_confirmation")
    if not isinstance(confirmation, dict):
        return _result(
            INCONCLUSIVE,
            "the GS724T port membership has not been confirmed from the web interface",
        )
    if confirmation.get("config_hash") != config_hash:
        return _result(FAIL, "the switch confirmation was made for a different configuration")
    if confirmation.get("phrase_ok") is not True:
        return _result(FAIL, "the switch confirmation phrase was not accepted")
    confirmed_at = confirmation.get("confirmed_at")
    if not isinstance(confirmed_at, str):
        return _result(INCONCLUSIVE, "the switch confirmation has no timestamp")
    try:
        moment = datetime.fromisoformat(confirmed_at)
    except ValueError:
        return _result(FAIL, "the switch confirmation timestamp is invalid")
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - moment.astimezone(timezone.utc)
    if age.total_seconds() < 0 or age.total_seconds() > 24 * 3600:
        return _result(INCONCLUSIVE, "the switch confirmation is older than 24 hours")
    return _result(PASS, "an administrator confirmed the GS724T VLAN membership for this configuration")
