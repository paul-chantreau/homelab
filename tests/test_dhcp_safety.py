"""DHCP stays off until validation passes and the operator confirms it."""

from datetime import datetime, timedelta, timezone

import pytest

from maas_deploy.acceptance import CRITICAL_CHECKS, evaluate
from maas_deploy.dhcp_gate import activation_phrase, assess_activation, deactivation_phrase
from maas_deploy.maas_network import PlanError, plan_disable, plan_enable, plan_initial, render_maas_command


def _facts(ready):
    return {
        "interfaces": [
            {
                "name": "enp1s0",
                "mac": "52:54:00:12:34:56",
                "operstate": "UP",
                "addresses": ["192.0.2.10/24"],
                "vlan_id": None,
            },
            {
                "name": "provisioning",
                "mac": "52:54:00:12:34:56",
                "operstate": "UP",
                "addresses": ["198.51.100.2/24"],
                "vlan_id": 100,
            },
        ],
        "default_route": {"via": "192.0.2.1", "device": "enp1s0"},
        "management_gateway": {"reachable": True, "device": "enp1s0"},
        "test_host": {
            "address": "198.51.100.50",
            "ping": True,
            "arping": True,
            "reverse_tcp": True,
        },
        "isolation_report": {
            "captured_on_maas_server": False,
            "vlan_id": 100,
            "observed_host": "198.51.100.50",
            "seconds": 30,
            "packet_count": 0,
        },
        "dhcp_capture": {"ran": True, "offer_count": 0},
        "maas_rack": {"present": True, "region_active": True, "rack_active": True, "dhcp_active": False},
        "switch_confirmation": {
            "phrase_ok": True,
            "config_hash": ready.canonical_hash,
            "confirmed_at": datetime.now(timezone.utc).isoformat(),
        },
    }


def _inventory():
    return {
        "subnets": [
            {
                "id": 1,
                "cidr": "198.51.100.0/24",
                "vlan": {"fabric_id": 1, "vid": 100, "dhcp_on": False},
            },
            {
                "id": 2,
                "cidr": "192.0.2.0/24",
                "vlan": {"fabric_id": 0, "vid": 1, "dhcp_on": True},
            },
        ],
        "vlans": [
            {"id": 10, "fabric_id": 1, "vid": 100, "dhcp_on": False},
            {"id": 11, "fabric_id": 0, "vid": 1, "dhcp_on": True},
        ],
        "ipranges": [],
        "racks": [{"system_id": "rack1"}],
    }


def test_passing_validation_is_required_and_expires(ready):
    report = evaluate(ready.network, _facts(ready), ready.canonical_hash)
    assert report["outcome"] == "PASS"
    assert report["blocks_dhcp"] is False
    report["generated_at"] = datetime.now(timezone.utc).isoformat()
    allowed, _reason = assess_activation(report, ready.canonical_hash)
    assert allowed is True
    stale = dict(report)
    stale["generated_at"] = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    allowed, reason = assess_activation(stale, ready.canonical_hash)
    assert allowed is False
    assert "older" in reason


def test_inconclusive_isolation_blocks_dhcp(ready):
    facts = _facts(ready)
    del facts["isolation_report"]
    report = evaluate(ready.network, facts, ready.canonical_hash)
    assert report["outcome"] == "INCONCLUSIVE"
    assert report["checks"]["vlan_isolation"]["status"] == "INCONCLUSIVE"
    report["generated_at"] = datetime.now(timezone.utc).isoformat()
    allowed, reason = assess_activation(report, ready.canonical_hash)
    assert allowed is False
    assert "INCONCLUSIVE" in reason


def test_unexpected_dhcp_and_leaked_vlan_fail(ready):
    facts = _facts(ready)
    facts["dhcp_capture"] = {"ran": True, "offer_count": 1}
    facts["isolation_report"]["packet_count"] = 2
    report = evaluate(ready.network, facts, ready.canonical_hash)
    assert report["outcome"] == "FAIL"
    assert set(report["checks"]) == set(CRITICAL_CHECKS)


def test_initial_plan_disables_management_dhcp_and_does_not_enable_provisioning(ready):
    actions = plan_initial(ready.network, _inventory())
    rendered = [" ".join(render_maas_command("admin", action)) for action in actions]
    assert any("dhcp_on=false" in command and " 0 1 " in f" {command} " or command.endswith("0 1 dhcp_on=false") for command in rendered)
    assert not any("dhcp_on=true" in command for command in rendered)
    management = [command for command in rendered if "dhcp_on=false" in command and " 0 1 " in f" {command} "]
    assert management
    ranges = [action for action in actions if action["op"] == "iprange-create"]
    assert {item["args"]["type"] for item in ranges} == {"reserved", "dynamic"}
    assert any(item["args"]["start_ip"] == "198.51.100.2" for item in ranges)


def test_enable_and_disable_target_only_the_provisioning_vlan(ready):
    enabled = plan_enable(ready.network, _inventory())
    command = render_maas_command("admin", enabled[0])
    assert command == [
        "maas",
        "admin",
        "vlan",
        "update",
        "1",
        "100",
        "dhcp_on=true",
        "primary_rack=rack1",
    ]
    assert activation_phrase(100) == "ACTIVATE DHCP ON VLAN 100"
    assert deactivation_phrase(100) == "DEACTIVATE DHCP ON VLAN 100"
    disabled = plan_disable(ready.network, _inventory())
    assert render_maas_command("admin", disabled[0])[-1] == "dhcp_on=false"
    assert "100" in render_maas_command("admin", disabled[0])


def test_enable_refuses_a_missing_rack(ready):
    inventory = _inventory()
    inventory["racks"] = []
    with pytest.raises(PlanError):
        plan_enable(ready.network, inventory)
