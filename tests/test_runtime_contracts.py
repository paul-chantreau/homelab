"""Exercise production orchestration at the MAAS 3.7 CLI/API boundary."""
import copy
import json
from types import SimpleNamespace

import pytest

from maas_deploy import runtime
from maas_deploy.facts import parse_maas_status
from maas_deploy.maas_network import PlanError, plan_initial, plan_enable
from test_dhcp_safety import _inventory

PEBBLE = 'Service Startup Current Since Notes\nregiond enabled active today -\nrackd enabled active today -\ndhcpd disabled inactive - -\n'


def result(stdout='', code=0):
    return SimpleNamespace(stdout=stdout, stderr='', returncode=code)


def test_fresh_install_does_not_call_maas_until_snap_is_installed(ready, tmp_path, monkeypatch):
    installed = False
    calls = []
    def run(command, **kwargs):
        nonlocal installed
        calls.append(command)
        if command[:3] == ['snap', 'list', 'maas']:
            return result(code=1)
        if command[:3] == ['snap', 'install', 'maas']:
            installed = True
        if command[0] == 'maas':
            assert installed, 'MAAS was executed before installation'
        if command[:2] == ['maas', 'apikey']:
            return result('consumer:token:secret')
        assert not ('set-config' in command and 'name=maas_url' in command)
        return result()
    monkeypatch.setattr(runtime, '_run', run)
    monkeypatch.setattr(runtime, '_wait_for_maas', lambda: None)
    runtime.apply_maas(ready, tmp_path / 'secrets.env')
    assert any(command[:2] == ['maas', 'init'] for command in calls)
    assert any(command[:2] == ['maas', 'createadmin'] for command in calls)


def test_rerun_preserves_database_initialization_and_admin(ready, tmp_path, monkeypatch):
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        if command[:2] == ['snap', 'info']:
            return result('tracking: 3.7/stable\n')
        if command[:3] == ['maas', 'config', '--show']:
            return result('mode=region+rack\nmaas_url=http://198.51.100.2:5240/MAAS/\n')
        if command[:2] == ['maas', 'apikey']:
            return result('consumer:token:secret')
        return result()
    monkeypatch.setattr(runtime, '_run', run)
    monkeypatch.setattr(runtime, '_wait_for_maas', lambda: None)
    runtime.apply_maas(ready, tmp_path / 'secrets.env')
    assert not any(c[:2] in [['maas', 'init'], ['maas', 'createadmin']] for c in calls)
    assert not any(c[:3] == ['snap', 'install', 'maas'] for c in calls)


def test_pebble_columns_and_legacy_output_are_read_correctly():
    status = parse_maas_status(PEBBLE)
    assert status['region_active'] and status['rack_active'] and not status['dhcp_active']
    assert parse_maas_status('rackd RUNNING\n')['rack_active']
    assert not parse_maas_status('not a service listing')['present']


def test_service_listing_alone_is_not_api_readiness(monkeypatch):
    from urllib.error import URLError
    monkeypatch.setattr(runtime, '_run', lambda *a, **k: result(PEBBLE))
    def unavailable(*a, **k):
        raise URLError('not ready')
    monkeypatch.setattr(runtime, 'urlopen', unavailable)
    ticks = iter([0, 1, 301])
    monkeypatch.setattr(runtime.time, 'monotonic', lambda: next(ticks))
    monkeypatch.setattr(runtime.time, 'sleep', lambda _: None)
    with pytest.raises(runtime.SetupError, match='ready'):
        runtime._wait_for_maas()


def test_initial_subnet_is_managed_and_bound_to_discovered_vlan(ready):
    inventory = _inventory()
    inventory['subnets'] = []
    actions = plan_initial(ready.network, inventory)
    create = next(a for a in actions if a['op'] == 'subnet-create')
    assert create['args']['vlan'] == 10
    assert create['args']['managed'] == 'true'
    assert not any(a.get('args', {}).get('dhcp_on') == 'true' for a in actions)


def test_same_vid_on_another_fabric_does_not_change_selected_vlan(ready):
    inventory = _inventory()
    inventory['vlans'].insert(0, {'id': 99, 'fabric_id': 9, 'vid': 100})
    assert plan_enable(ready.network, inventory)[0]['fabric_id'] == 1


@pytest.mark.parametrize('change', ['wrong-subnet', 'unmanaged', 'wrong-parent', 'missing-range'])
def test_inconsistent_topology_is_rejected(ready, change):
    inventory = _inventory()
    if change == 'wrong-subnet':
        inventory['subnets'][0]['vlan']['id'] = 11
    elif change == 'unmanaged':
        inventory['subnets'][0]['managed'] = False
    elif change == 'wrong-parent':
        inventory['interfaces'][1]['parents'] = ['other0']
    else:
        inventory['ipranges'] = []
    with pytest.raises(PlanError):
        plan_enable(ready.network, inventory)


@pytest.mark.parametrize('state', ['ready', 'range-missing', 'enable-failed'])
def test_network_setup_creates_ranges_before_automatic_dhcp(ready, tmp_path, monkeypatch, state):
    initial = _inventory()
    initial['subnets'] = initial['subnets'][1:]
    initial['ipranges'] = []
    configured = _inventory()
    configured['vlans'][1]['dhcp_on'] = False
    configured['subnets'][0]['id'] = 42
    configured['ipranges'][0]['subnet']['id'] = 42
    if state == 'range-missing':
        configured['ipranges'] = []
    final = copy.deepcopy(configured)
    final['vlans'][0]['dhcp_on'] = state != 'enable-failed'
    snapshots = iter([initial, configured, final])
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return result(json.dumps({'id': 42}) if command[2:4] == ['subnets', 'create'] else '')

    monkeypatch.setattr(runtime, 'load', lambda _: {'MAAS_API_KEY': 'consumer:token:secret'})
    monkeypatch.setattr(runtime, '_run', run)
    monkeypatch.setattr(runtime, 'inventory', lambda _: copy.deepcopy(next(snapshots)))
    if state == 'ready':
        assert runtime.apply_network(ready, tmp_path / 'secrets.env', tmp_path) == 'ready'
    elif state == 'range-missing':
        with pytest.raises(PlanError, match='dynamic range'):
            runtime.apply_network(ready, tmp_path / 'secrets.env', tmp_path)
    else:
        with pytest.raises(runtime.SetupError, match='dhcp_on=False'):
            runtime.apply_network(ready, tmp_path / 'secrets.env', tmp_path)

    ranges = [command for command in calls if command[2:4] == ['ipranges', 'create']]
    assert len(ranges) == 2
    assert all('subnet=42' in command for command in ranges)
    enabled = [command for command in calls if 'dhcp_on=true' in command]
    if state == 'range-missing':
        assert enabled == []
    else:
        assert len(enabled) == 1
        assert enabled[0][4:6] == ['1', '100']
        assert all(calls.index(command) < calls.index(enabled[0]) for command in ranges)


def test_incomplete_or_wrong_architecture_images_cannot_be_ready():
    resource = {'name': 'ubuntu/noble', 'type': 'Synced', 'architecture': 'amd64/generic', 'sets': {}}
    assert not runtime._image_synced([resource], 'noble')
    resource['sets'] = {'v1': {'complete': True, 'files': {'root': {'complete': True}}}}
    assert runtime._image_synced([resource], 'noble')
    resource['architecture'] = 'arm64/generic'
    assert not runtime._image_synced([resource], 'noble')


def test_cached_marker_is_revalidated_against_resource_details(ready, tmp_path, monkeypatch):
    from maas_deploy.secrets import ensure, store_api_key
    secrets = tmp_path / 'secrets.env'
    ensure(secrets)
    store_api_key(secrets, 'consumer:token:secret')
    marker = tmp_path / 'images-synced'
    marker.write_text('synced\n')
    monkeypatch.setattr(runtime, '_run', lambda *a, **k: result())
    def resources(*a):
        raise runtime.SetupError('resource inspection attempted')
    monkeypatch.setattr(runtime, '_image_resources', resources)
    with pytest.raises(runtime.SetupError, match='inspection attempted'):
        runtime.apply_images(ready, secrets, marker)


@pytest.mark.parametrize('disabled,dhcp_on,expected_status', [
    (False, True, 'pass'), (False, False, 'fail'),
    (True, False, 'pass'), (True, True, 'fail'),
])
def test_health_checks_default_dhcp_and_operator_stop(ready, tmp_path, monkeypatch, disabled, dhcp_on, expected_status):
    from maas_deploy.healthcheck import collect, evaluate
    from maas_deploy.secrets import ensure, store_api_key
    secrets = tmp_path / 'secrets.env'
    ensure(secrets)
    store_api_key(secrets, 'consumer:token:secret')
    if disabled:
        (tmp_path / 'dhcp-disabled').write_text('vlan 100\n')
    inventory = _inventory()
    inventory['vlans'][0]['dhcp_on'] = dhcp_on
    inventory['vlans'][1]['dhcp_on'] = False
    monkeypatch.setattr(runtime, 'inventory', lambda *a: inventory)
    def run(command, **kwargs):
        if command[:2] == ['maas', 'status']:
            return result(PEBBLE.replace('dhcpd disabled inactive - -', 'dhcpd enabled active today -'))
        return result()
    monkeypatch.setattr(runtime, '_run', run)
    snapshot = collect(ready, secrets, tmp_path)
    assert snapshot['management_dhcp_on'] is False
    assert snapshot['provisioning_dhcp_on'] is dhcp_on
    assert snapshot['provisioning_dhcp_expected'] is not disabled
    assert evaluate(snapshot)['checks']['provisioning_dhcp'] == expected_status
