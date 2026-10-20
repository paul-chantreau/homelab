"""Schema and safety checks for the canonical network configuration."""

import json
from pathlib import Path

import jsonschema
import pytest

from maas_deploy.config import INSTALL_MODE, SCHEMA_MODE, ConfigError, validate
from maas_deploy.actions_config import catalog

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
    schema = json.loads((ROOT / "config/schema/network.schema.json").read_text())
    jsonschema.validate({"network": ready.network}, schema)


def test_every_network_input_is_backed_by_a_required_actions_secret(committed):
    def leaves(mapping, prefix):
        for key, value in mapping.items():
            path = f"{prefix}.{key}"
            if isinstance(value, dict):
                yield from leaves(value, path)
            else:
                yield path

    network_inputs = {name: spec for name, spec in catalog().items() if spec['path'].startswith('network.')}
    assert len(network_inputs) == 10
    assert set(leaves(committed.network, 'network')) == {spec['path'] for spec in network_inputs.values()}
    assert all(spec.get('required') and spec['source'] == 'secret' and 'default' not in spec
               for spec in network_inputs.values())


@pytest.mark.parametrize('path,value', [
    (('mode',), 'vlan'),
    (('routing',), {'decision': 'isolated', 'ubuntu_nat': False}),
    (('switch',), {'hardware_revision': 'GS724Tv4', 'router_port': 1}),
    (('provisioning', 'dhcp', 'activate_automatically'), False),
    (('provisioning', 'gateway'), None),
    (('provisioning', 'dns'), []),
    (('provisioning', 'acceptance'), {'test_host': '198.51.100.50'}),
])
def test_obsolete_fields_are_rejected_by_validator_and_schema(ready, path, value):
    mapping = ready.network
    for key in path[:-1]:
        mapping = mapping[key]
    mapping[path[-1]] = value
    with pytest.raises(ConfigError, match='unknown fields'):
        validate(ready, INSTALL_MODE)
    schema = json.loads((ROOT / 'config/schema/network.schema.json').read_text())
    with pytest.raises(jsonschema.ValidationError, match='Additional properties'):
        jsonschema.validate({'network': ready.network}, schema)


@pytest.mark.parametrize(
    ("mutate", "fragment"),
    [
        (lambda network, _deployment: network["management"].update({"address": "198.51.100.10/24"}), "overlap"),
        (lambda network, _deployment: network["provisioning"]["dhcp"].update({"range_start": "198.51.100.2"}), "outside the dynamic"),
        (lambda network, _deployment: network["provisioning"]["dhcp"].update({"range_end": "192.0.2.20"}), "inside the provisioning"),
        (lambda network, _deployment: network["management"].update({"gateway": "198.51.100.1"}), "management subnet"),
        (lambda network, _deployment: network["management"].update({"interface_mac": "01:00:00:00:00:01"}), "unicast"),
        (lambda network, _deployment: network["management"].update({"vlan_id": 100}), "must be different"),
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
