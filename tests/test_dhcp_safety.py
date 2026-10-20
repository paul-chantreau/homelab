"""Provisioning DHCP starts automatically and retains explicit operator controls."""

import copy
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

import pytest
import yaml

from maas_deploy import cli, runtime
from maas_deploy.maas_network import PlanError, plan_disable, plan_enable, plan_initial, render_maas_command


def _inventory():
    return {
        "subnets": [
            {
                "id": 1,
                "cidr": "198.51.100.0/24",
                "managed": True,
                "vlan": {"id": 10, "fabric_id": 1, "vid": 100, "dhcp_on": False},
            },
            {
                "id": 2,
                "cidr": "192.0.2.0/24",
                "vlan": {"id": 11, "fabric_id": 0, "vid": 1, "dhcp_on": True},
            },
        ],
        "vlans": [
            {"id": 10, "fabric_id": 1, "vid": 100, "dhcp_on": False},
            {"id": 11, "fabric_id": 0, "vid": 1, "dhcp_on": True},
        ],
        "interfaces": [
            {"id": 1, "name": "eth0", "type": "physical", "mac_address": "52:54:00:12:34:56", "vlan": {"id": 11, "vid": 0}},
            {"id": 2, "name": "provisioning", "type": "vlan", "parents": ["eth0"], "mac_address": "52:54:00:12:34:56", "vlan": {"id": 10, "vid": 100}},
        ],
        "ipranges": [{"subnet": {"id": 1}, "type": "dynamic", "start_ip": "198.51.100.100", "end_ip": "198.51.100.200"}],
        "racks": [{"system_id": "rack1"}],
    }


def test_subnet_reconciliation_disables_management_dhcp_before_activation(ready):
    actions = plan_initial(ready.network, _inventory())
    rendered = [" ".join(render_maas_command("admin", action)) for action in actions]
    assert any("dhcp_on=false" in command and " 0 1 " in f" {command} " or command.endswith("0 1 dhcp_on=false") for command in rendered)
    assert not any("dhcp_on=true" in command for command in rendered)
    management = [command for command in rendered if "dhcp_on=false" in command and " 0 1 " in f" {command} "]
    assert management
    ranges = [action for action in actions if action["op"] == "iprange-create"]
    assert {item["args"]["type"] for item in ranges} == {"reserved"}
    assert any(item["args"]["start_ip"] == "198.51.100.2" for item in ranges)


def test_rerun_clears_stale_provisioning_routes_and_dns_without_disabling_activated_dhcp(ready):
    inventory = _inventory()
    inventory['subnets'][0].update(gateway_ip='198.51.100.1', dns_servers=['192.0.2.1'])
    inventory['subnets'][0]['vlan']['dhcp_on'] = True
    inventory['vlans'][0]['dhcp_on'] = True
    actions = plan_initial(ready.network, inventory)
    updates = [action['args'] for action in actions if action['op'] == 'subnet-update' and action['id'] == 1]
    assert {'gateway_ip': '', 'dns_servers': '', 'managed': 'true'} in updates
    assert not any(action['op'] == 'vlan-update' and action['vid'] == 100 for action in actions)


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
    disabled = plan_disable(ready.network, _inventory())
    assert render_maas_command("admin", disabled[0])[-1] == "dhcp_on=false"
    assert "100" in render_maas_command("admin", disabled[0])


def test_enable_refuses_a_missing_rack(ready):
    inventory = _inventory()
    inventory["racks"] = []
    with pytest.raises(PlanError):
        plan_enable(ready.network, inventory)


@pytest.fixture
def dhcp_cli(ready, tmp_path, monkeypatch):
    current = _inventory()
    state = tmp_path / 'state'
    calls = []

    def run(command):
        calls.append(command)
        if command[:4] == ['maas', 'admin', 'vlan', 'update']:
            target = next(vlan for vlan in current['vlans'] if vlan['vid'] == int(command[5]))
            target['dhcp_on'] = 'dhcp_on=true' in command
        return subprocess.CompletedProcess(command, 0, stdout='')

    monkeypatch.setattr(cli, '_config', lambda: ready)
    monkeypatch.setattr(cli, '_state', lambda: state)
    monkeypatch.setattr(runtime, 'load', lambda _: {'MAAS_API_KEY': 'consumer:token:secret'})
    monkeypatch.setattr(runtime, '_run', run)
    monkeypatch.setattr(runtime, 'inventory', lambda _: copy.deepcopy(current))
    return calls, current, state


@pytest.mark.parametrize('old_report', [False, True])
def test_activation_needs_no_validation_report(dhcp_cli, monkeypatch, old_report):
    calls, current, state = dhcp_cli
    if old_report:
        state.mkdir()
        (state / 'vlan-validation.json').write_text('obsolete, unreadable report')
    monkeypatch.setenv('MAAS_DHCP_CONFIRM', 'ACTIVATE DHCP ON VLAN 100')

    assert cli.main(['dhcp-enable']) == 0
    assert not (state / 'dhcp-disabled').exists()
    assert calls[1:] == [
        ['maas', 'admin', 'vlan', 'update', '1', '100', 'dhcp_on=true', 'primary_rack=rack1']
    ]
    assert current['vlans'][1]['dhcp_on'] is True  # management VLAN is untouched


@pytest.mark.parametrize('command,phrase', [
    ('dhcp-enable', ''),
    ('dhcp-enable', 'ACTIVATE DHCP ON VLAN 1'),
    ('dhcp-disable', 'ACTIVATE DHCP ON VLAN 100'),
])
def test_missing_or_wrong_confirmation_changes_nothing(dhcp_cli, monkeypatch, command, phrase):
    calls, _, state = dhcp_cli
    monkeypatch.setenv('MAAS_DHCP_CONFIRM', phrase)
    assert cli.main([command]) == 1
    assert calls == []
    assert not state.exists()


@pytest.mark.parametrize('defect', ['no-rack', 'wrong-mac', 'wrong-parent', 'wrong-subnet', 'unmanaged', 'no-range'])
def test_activation_refuses_inconsistent_maas_inventory(dhcp_cli, monkeypatch, capsys, defect):
    calls, current, state = dhcp_cli
    if defect == 'no-rack':
        current['racks'] = []
    elif defect == 'wrong-mac':
        current['interfaces'][0]['mac_address'] = '52:54:00:00:00:01'
    elif defect == 'wrong-parent':
        current['interfaces'][1]['parents'] = ['other0']
    elif defect == 'wrong-subnet':
        current['subnets'][0]['vlan']['id'] = 11
    elif defect == 'unmanaged':
        current['subnets'][0]['managed'] = False
    else:
        current['ipranges'] = []
    monkeypatch.setenv('MAAS_DHCP_CONFIRM', 'ACTIVATE DHCP ON VLAN 100')

    assert cli.main(['dhcp-enable']) == 1
    assert len(calls) == 1 and calls[0][:2] == ['maas', 'login']
    assert not (state / 'dhcp-disabled').exists()
    assert capsys.readouterr().err


def test_invalid_configuration_cannot_activate_dhcp(ready, dhcp_cli, monkeypatch):
    calls, _, state = dhcp_cli
    ready.network['provisioning']['vlan_id'] = ready.network['management']['vlan_id']
    monkeypatch.setenv('MAAS_DHCP_CONFIRM', 'ACTIVATE DHCP ON VLAN 1')
    assert cli.main(['dhcp-enable']) == 1
    assert calls == []
    assert not state.exists()


def test_failed_maas_enable_does_not_record_success(dhcp_cli, monkeypatch):
    calls, _, state = dhcp_cli
    monkeypatch.setattr(runtime, '_run', lambda command: calls.append(command))
    monkeypatch.setenv('MAAS_DHCP_CONFIRM', 'ACTIVATE DHCP ON VLAN 100')
    assert cli.main(['dhcp-enable']) == 1
    assert not (state / 'dhcp-disabled').exists()


@pytest.mark.parametrize('emergency', [False, True])
def test_deactivation_still_works_without_reports(dhcp_cli, monkeypatch, emergency):
    calls, current, state = dhcp_cli
    current['vlans'][0]['dhcp_on'] = True
    state.mkdir()
    monkeypatch.setenv('MAAS_DHCP_EMERGENCY', '1' if emergency else '0')
    monkeypatch.setenv('MAAS_DHCP_CONFIRM', ('EMERGENCY ' if emergency else '') + 'DEACTIVATE DHCP ON VLAN 100')

    assert cli.main(['dhcp-disable']) == 0
    assert (state / 'dhcp-disabled').read_text() == 'vlan 100\n'
    assert calls[1:] == [['maas', 'admin', 'vlan', 'update', '1', '100', 'dhcp_on=false']]
    assert current['vlans'][1]['dhcp_on'] is True


def test_automatic_setup_enables_only_provisioning_dhcp(ready, dhcp_cli):
    calls, current, state = dhcp_cli
    assert runtime.apply_network(ready, Path('unused-secrets.env'), state) == 'ready'
    assert current['vlans'][0]['dhcp_on'] is True
    assert current['vlans'][1]['dhcp_on'] is False
    assert not (state / 'dhcp-disabled').exists()
    enabled = [command for command in calls if 'dhcp_on=true' in command]
    assert enabled == [['maas', 'admin', 'vlan', 'update', '1', '100', 'dhcp_on=true', 'primary_rack=rack1']]
    management_stop = ['maas', 'admin', 'vlan', 'update', '0', '1', 'dhcp_on=false']
    assert calls.index(management_stop) < calls.index(enabled[0])


def test_manual_stop_persists_across_setup_reruns(ready, dhcp_cli, monkeypatch):
    calls, current, state = dhcp_cli
    monkeypatch.setenv('MAAS_DHCP_CONFIRM', 'DEACTIVATE DHCP ON VLAN 100')
    assert cli.main(['dhcp-disable']) == 0
    calls.clear()
    assert runtime.apply_network(ready, Path('unused-secrets.env'), state) == 'ready'
    assert current['vlans'][0]['dhcp_on'] is False
    assert (state / 'dhcp-disabled').exists()
    assert not any('dhcp_on=true' in command for command in calls)


def test_manual_activation_clears_a_persistent_stop(dhcp_cli, monkeypatch):
    _, _, state = dhcp_cli
    state.mkdir()
    (state / 'dhcp-disabled').write_text('vlan 100\n')
    monkeypatch.setenv('MAAS_DHCP_CONFIRM', 'ACTIVATE DHCP ON VLAN 100')
    assert cli.main(['dhcp-enable']) == 0
    assert not (state / 'dhcp-disabled').exists()


def test_failed_stop_still_prevents_automatic_reactivation(dhcp_cli, monkeypatch):
    _, _, state = dhcp_cli
    def unavailable(command):
        raise runtime.SetupError('MAAS is unavailable')
    monkeypatch.setattr(runtime, '_run', unavailable)
    monkeypatch.setenv('MAAS_DHCP_CONFIRM', 'DEACTIVATE DHCP ON VLAN 100')
    assert cli.main(['dhcp-disable']) == 1
    assert (state / 'dhcp-disabled').read_text() == 'vlan 100\n'


@pytest.mark.parametrize('extra_args', [[], ['--isolation-report', 'obsolete.json']])
def test_activation_wrapper_only_needs_the_confirmation(ready, tmp_path, extra_args):
    root = Path(__file__).resolve().parents[1]
    config_dir = tmp_path / 'config'
    config_dir.mkdir()
    (config_dir / 'network.yaml').write_text(yaml.safe_dump({'network': ready.network}))
    (config_dir / 'deployment.yaml').write_text(yaml.safe_dump({'deployment': ready.deployment}))
    bindir = tmp_path / 'bin'
    bindir.mkdir()
    (bindir / 'id').write_text('#!/bin/sh\necho 0\n')
    (bindir / 'id').chmod(0o755)
    record = tmp_path / 'invocation.json'
    python = bindir / 'python3'
    record_command = (
        'import json, os, sys; from pathlib import Path; '
        'Path(os.environ["INVOCATION_RECORD"]).write_text('
        'json.dumps([sys.argv[1:], os.environ.get("MAAS_DHCP_CONFIRM")]))'
    )
    python.write_text(
        '#!/bin/bash\n'
        f'if [[ "$1" == "-" ]]; then exec {shlex.quote(sys.executable)} "$@"; fi\n'
        f'exec {shlex.quote(sys.executable)} -c {shlex.quote(record_command)} "$@"\n'
    )
    python.chmod(0o755)
    command = bindir / 'maas-network-activate'
    command.symlink_to(root / 'scripts/activate-dhcp.sh')
    env = {**os.environ, 'MAAS_DEPLOY_ROOT': str(tmp_path), 'INVOCATION_RECORD': str(record),
           'PATH': str(bindir) + ':' + os.environ['PATH']}
    result = subprocess.run(['bash', str(command), *extra_args], cwd=root, env=env,
                            input='ACTIVATE DHCP ON VLAN 100\n', capture_output=True, text=True)

    if extra_args:
        assert result.returncode == 1 and 'Usage:' in result.stderr
        assert not record.exists()
    else:
        assert result.returncode == 0, result.stderr
        assert 'Dynamic range: 198.51.100.100 - 198.51.100.200' in result.stdout
        assert json.loads(record.read_text()) == [['-m', 'maas_deploy', 'dhcp-enable'], 'ACTIVATE DHCP ON VLAN 100']
