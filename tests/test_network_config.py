"""Schema and safety checks for the canonical network configuration."""

import copy
import json
from pathlib import Path

import jsonschema
import pytest
import yaml

from maas_deploy.config import INSTALL_MODE, SCHEMA_MODE, ConfigError, validate

ROOT = Path(__file__).resolve().parents[1]


def test_committed_configuration_matches_schema_and_is_not_installable(committed):
    network_schema = jsonschema.Draft202012Validator(
        json.loads((ROOT / "config" / "schema" / "network.schema.json").read_text(encoding="utf-8"))
    )
    deployment_schema = jsonschema.Draft202012Validator(
        json.loads((ROOT / "config" / "schema" / "deployment.schema.json").read_text(encoding="utf-8"))
    )
    network_schema.validate({"network": committed.network})
    deployment_schema.validate({"deployment": committed.deployment})
    validate(committed, SCHEMA_MODE)
    with pytest.raises(ConfigError):
        validate(committed, INSTALL_MODE)


def test_ready_configuration_passes_install_mode(ready):
    validate(ready, INSTALL_MODE)


@pytest.mark.parametrize(
    ("mutate", "fragment"),
    [
        (lambda network, _deployment: network.update({"mode": "shared-lan"}), "shared"),
        (lambda network, _deployment: network["routing"].update({"ubuntu_nat": True}), "ubuntu_nat"),
        (lambda network, _deployment: network["routing"].update({"decision": "switch-l3"}), "isolated"),
        (lambda network, _deployment: network["provisioning"]["dhcp"].update({"activate_automatically": True}), "activate_automatically"),
        (lambda network, _deployment: network["management"].update({"address": "198.51.100.10/24"}), "overlap"),
        (lambda network, _deployment: network["provisioning"]["dhcp"].update({"range_start": "198.51.100.2"}), "outside the dynamic"),
        (lambda network, _deployment: network["provisioning"]["dhcp"].update({"range_end": "192.0.2.20"}), "inside the provisioning"),
        (lambda network, _deployment: network["management"].update({"gateway": "198.51.100.1"}), "management subnet"),
        (lambda network, _deployment: network["provisioning"].update({"gateway": "198.51.100.150"}), "outside the dynamic"),
        (lambda network, _deployment: network["management"].update({"interface_mac": "01:00:00:00:00:01"}), "unicast"),
        (lambda network, _deployment: network["switch"].update({"router_port": 2}), "unique"),
        (lambda network, _deployment: network["switch"].update({"hardware_revision": "GS724Tv3"}), "GS724Tv4"),
        (lambda network, _deployment: network["switch"].update({"provisioning_ports": [26, 27]}), "1 to 26"),
        (lambda network, _deployment: network["provisioning"].update({"vlan_id": 2}), "predefined"),
        (lambda _network, deployment: deployment["disk"].update({"serial": "/dev/sda"}), "unstable"),
        (lambda _network, deployment: deployment["disk"].update({"confirm_erase": False}), "confirm_erase"),
        (lambda _network, deployment: deployment["ssh_authorized_keys"].append("-----BEGIN OPENSSH PRIVATE KEY-----\nabc"), "private key"),
        (lambda _network, deployment: deployment.update({"maas": {**deployment["maas"], "mode": "rack"}}), "region+rack"),
    ],
)
def test_unsafe_values_are_rejected(ready, mutate, fragment):
    mutate(ready.network, ready.deployment)
    with pytest.raises(ConfigError) as caught:
        validate(ready, INSTALL_MODE)
    assert fragment in str(caught.value)


def test_model_alone_does_not_select_a_disk(ready):
    ready.deployment["disk"].update({"serial": None, "model": "Samsung SSD"})
    with pytest.raises(ConfigError, match="serial, wwn, or by_id"):
        validate(ready, INSTALL_MODE)
