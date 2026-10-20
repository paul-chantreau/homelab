"""Shared fixtures for configuration that is complete enough to install."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from maas_deploy.config import LoadedConfig, load_config

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_KEY = 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIJlcpwFckeppqZKV/Tdh039UG2PeCb3EBZjyQUXnv71E agent@3feae36e8d35'


@pytest.fixture
def committed() -> LoadedConfig:
    return load_config(ROOT / "config" / "network.yaml", ROOT / "config" / "deployment.yaml")


@pytest.fixture
def ready(committed: LoadedConfig) -> LoadedConfig:
    network = copy.deepcopy(committed.network)
    deployment = copy.deepcopy(committed.deployment)
    network["switch"].update(
        {
            "hardware_revision": "GS724Tv4",
            "firmware_version": "6.3.1.43",
            "router_port": 1,
            "maas_port": 2,
            "provisioning_ports": [3, 4],
        }
    )
    network["management"].update(
        {
            "interface_mac": "52:54:00:12:34:56",
            "address": "192.0.2.10/24",
            "gateway": "192.0.2.1",
            "dns": ["192.0.2.1"],
            "vlan_id": 1,
        }
    )
    network["provisioning"].update(
        {
            "vlan_id": 100,
            "subnet": "198.51.100.0/24",
            "controller_address": "198.51.100.2",
            "gateway": None,
            "dns": [],
        }
    )
    network["provisioning"]["dhcp"].update(
        {"activate_automatically": False, "range_start": "198.51.100.100", "range_end": "198.51.100.200"}
    )
    network["provisioning"]["acceptance"]["test_host"] = "198.51.100.50"
    deployment["ssh_authorized_keys"] = [PUBLIC_KEY]
    deployment["disk"].update({"serial": "DISKSERIAL1", "confirm_erase": True})
    return LoadedConfig(network=network, deployment=deployment)
