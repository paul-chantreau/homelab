"""Netplan and autoinstall rendering."""

import yaml

from maas_deploy.autoinstall import REFUSAL_SERIAL, render_autoinstall
from maas_deploy.netplan import render_netplan


def test_netplan_uses_mac_match_and_keeps_the_default_route_on_management(ready):
    document = yaml.safe_load(render_netplan(ready.network))
    nic = document["network"]["ethernets"]["maas_nic"]
    vlan = document["network"]["vlans"]["provisioning"]
    assert nic["match"]["macaddress"] == "52:54:00:12:34:56"
    assert nic["addresses"] == ["192.0.2.10/24"]
    assert nic["routes"] == [{"to": "default", "via": "192.0.2.1"}]
    assert nic["dhcp4"] is False
    assert vlan["id"] == 100
    assert vlan["link"] == "maas_nic"
    assert vlan["addresses"] == ["198.51.100.2/24"]
    assert "routes" not in vlan
    rendered = render_netplan(ready.network)
    assert "ip_forward" not in rendered
    assert "enp1s0" not in rendered


def test_incomplete_autoinstall_cannot_match_a_real_disk(committed):
    rendered = render_autoinstall(committed.network, committed.deployment, install_ready=False)
    document = yaml.safe_load(rendered)
    match = document["autoinstall"]["storage"]["layout"]["match"]
    assert match == {"serial": REFUSAL_SERIAL}
    assert "BEGIN OPENSSH PRIVATE KEY" not in rendered
    assert document["autoinstall"]["ssh"]["allow-pw"] is False


def test_ready_autoinstall_locks_the_account_and_names_the_disk(ready):
    rendered = render_autoinstall(ready.network, ready.deployment, install_ready=True)
    document = yaml.safe_load(rendered)
    autoinstall = document["autoinstall"]
    assert autoinstall["storage"]["layout"]["match"] == {"serial": REFUSAL_SERIAL}
    assert autoinstall["storage"]["layout"]["name"] == "direct"
    assert autoinstall["identity"]["password"].startswith("$6$")
    assert autoinstall["ssh"]["authorized-keys"] == ready.deployment["ssh_authorized_keys"]
    assert autoinstall["network"]["vlans"]["provisioning"]["id"] == 100
    assert "passwd -l maasadmin" in " ".join(autoinstall["late-commands"])
    assert "maas-network-activate" in " ".join(autoinstall["late-commands"])
