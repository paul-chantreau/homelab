"""Console discovery must not save topology or confuse a workstation with MAAS."""

import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from maas_deploy.discovery import _dns_for_interface, assemble, summary

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def snapshots():
    return {
        "address": [{"ifindex": 2, "ifname": "eth0", "addr_info": [
            {"family": "inet", "local": "192.0.2.20", "prefixlen": 24}]}],
        "link": [{"ifindex": 2, "ifname": "eth0", "address": "52:54:00:00:00:01", "operstate": "UP"}],
        "route": [{"dst": "default", "gateway": "192.0.2.1", "dev": "eth0"}],
        "resolvectl": "Link 2 (eth0): 192.0.2.1 2001:db8::1 127.0.0.53 192.0.2.1\n",
    }


def report_from(snapshots):
    report = assemble(snapshots['address'], snapshots['link'], snapshots['route'])
    report['dns'] = {'eth0': ['192.0.2.1', '2001:db8::1', '127.0.0.53', '192.0.2.1']}
    return report


def test_console_names_and_json_dns_do_not_reuse_workstation_identity(snapshots):
    output = summary(report_from(snapshots))
    assert 'LOCAL_ADDRESS=192.0.2.20/24' in output
    assert 'LOCAL_MAC=52:54:00:00:00:01' in output
    assert 'MANAGEMENT_GATEWAY=192.0.2.1' in output
    assert 'MANAGEMENT_DNS=["192.0.2.1"]' in output
    assert 'MANAGEMENT_INTERFACE_MAC=<MAAS_SERVER_ETHERNET_MAC>' in output
    assert 'MANAGEMENT_ADDRESS=<UNUSED_MAAS_SERVER_IPV4>/24' in output
    assert 'MANAGEMENT_VLAN_ID=<MANAGEMENT_VLAN_FROM_SWITCH_UI>' in output
    assert 'does not identify Netgear' in output


def test_multiple_default_interfaces_require_selection(snapshots):
    report = report_from(snapshots)
    report['interfaces'].append({'name': 'vpn0', 'mac': '', 'operstate': 'UP', 'addresses': ['198.51.100.2/24']})
    report['routes'].append({'destination': 'default', 'gateway': '198.51.100.1', 'device': 'vpn0'})
    output = summary(report)
    assert 'Rerun with --interface NAME' in output
    assert 'MANAGEMENT_GATEWAY=<CONFIRM_ROUTER_IPV4>' in output
    assert 'MANAGEMENT_DNS=<CONFIRM_IPV4_DNS_JSON_LIST>' in output
    chosen = summary(report, 'eth0')
    assert 'MANAGEMENT_GATEWAY=192.0.2.1' in chosen
    assert 'LOCAL_INTERFACE=eth0' in chosen
    with pytest.raises(ValueError, match='not up with an IPv4 address'):
        summary(report, 'missing')


def test_ambiguous_gateway_and_prefix_are_left_unknown(snapshots):
    report = report_from(snapshots)
    report['routes'].append({'destination': 'default', 'gateway': '192.0.2.2', 'device': 'eth0'})
    report['interfaces'][0]['addresses'].append('198.51.100.2/28')
    output = summary(report)
    assert 'MANAGEMENT_GATEWAY=<CONFIRM_ROUTER_IPV4>' in output
    assert 'MANAGEMENT_ADDRESS=<UNUSED_MAAS_SERVER_IPV4>/<PREFIX_LENGTH>' in output


def test_missing_network_information_stays_unknown():
    output = summary(assemble([], [], []))
    assert 'MANAGEMENT_GATEWAY=<CONFIRM_ROUTER_IPV4>' in output
    assert 'MANAGEMENT_DNS=<CONFIRM_IPV4_DNS_JSON_LIST>' in output
    assert 'LOCAL_ADDRESS=' not in output


def test_dns_is_collected_for_selected_interface_and_filters_stubs(monkeypatch):
    def run(command, **kwargs):
        assert command == ['resolvectl', 'dns', 'eth0']
        return subprocess.CompletedProcess(command, 0, 'Link 2 (eth0): 127.0.0.53 2001:db8::1 192.0.2.1 192.0.2.1\n')
    monkeypatch.setattr(subprocess, 'run', run)
    assert _dns_for_interface('eth0') == ['192.0.2.1']


def test_dns_fallback_does_not_export_local_stub_or_ipv6(monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError('resolvectl')
    monkeypatch.setattr(subprocess, 'run', missing)
    monkeypatch.setattr(Path, 'read_text', lambda *args, **kwargs: 'nameserver 127.0.0.53\nnameserver 2001:db8::1\nnameserver 192.0.2.1\n')
    assert _dns_for_interface('eth0') == ['192.0.2.1']


def test_linux_script_prints_secrets_and_creates_no_files(tmp_path, snapshots):
    bindir = tmp_path / 'bin'
    bindir.mkdir()
    ip = bindir / 'ip'
    ip.write_text(f'#!{sys.executable}\nimport json, os, sys\ndata=json.loads(os.environ["HOMELAB_DISCOVERY_FIXTURE"])\nprint(json.dumps(data[sys.argv[-1]]))\n')
    resolver = bindir / 'resolvectl'
    resolver.write_text(f'#!{sys.executable}\nimport json, os\nprint(json.loads(os.environ["HOMELAB_DISCOVERY_FIXTURE"])["resolvectl"])\n')
    ip.chmod(0o755)
    resolver.chmod(0o755)
    work = tmp_path / 'work'
    work.mkdir()
    result = subprocess.run(['bash', str(ROOT / 'scripts/discover-network.sh')], cwd=work,
                            env={**os.environ, 'PATH': f'{bindir}:{os.environ["PATH"]}',
                                 'HOMELAB_DISCOVERY_FIXTURE': json.dumps(snapshots)},
                            text=True, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert 'MANAGEMENT_GATEWAY=192.0.2.1' in result.stdout
    assert 'MANAGEMENT_DNS=["192.0.2.1"]' in result.stdout
    assert list(work.iterdir()) == []
    assert 'Wrote ' not in result.stdout


@pytest.fixture
def windows_fixture():
    return {
        'adapters': [{'ifIndex': 2, 'Status': 'Up', 'MacAddress': '52-54-00-00-00-01'}],
        'configurations': [{'InterfaceAlias': 'Ethernet', 'InterfaceIndex': 2,
                            'IPv4Address': [{'IPAddress': '192.0.2.20', 'PrefixLength': 24}],
                            'IPv4DefaultGateway': [{'NextHop': '192.0.2.1'}]}],
        'dns': [{'InterfaceIndex': 2, 'ServerAddresses': ['192.0.2.1', '127.0.0.53', '2001:db8::1']}],
    }


@pytest.fixture
def run_powershell(tmp_path):
    executable = shutil.which('pwsh')
    if executable is None:
        pytest.skip('PowerShell is not installed')
    wrapper = tmp_path / 'mock-network.ps1'
    wrapper.write_text('''param([string]$ChosenInterface)
$fixture = $env:HOMELAB_DISCOVERY_FIXTURE | ConvertFrom-Json
function Get-NetAdapter { $fixture.adapters }
function Get-NetIPConfiguration { $fixture.configurations }
function Get-DnsClientServerAddress { param([string]$AddressFamily) $fixture.dns }
$options = @{}
if ($ChosenInterface) { $options.InterfaceAlias = $ChosenInterface }
& $env:HOMELAB_DISCOVERY_SCRIPT @options
''')
    work = tmp_path / 'work'
    work.mkdir()
    def run(fixture, interface=None):
        command = [executable, '-NoProfile', '-NonInteractive', '-File', str(wrapper)]
        if interface:
            command.extend(['-ChosenInterface', interface])
        result = subprocess.run(command, cwd=work,
                                env={**os.environ, 'HOMELAB_DISCOVERY_FIXTURE': json.dumps(fixture),
                                     'HOMELAB_DISCOVERY_SCRIPT': str(ROOT / 'scripts/discover-network.ps1'),
                                     'XDG_CACHE_HOME': str(tmp_path / 'cache'),
                                     'XDG_CONFIG_HOME': str(tmp_path / 'config'),
                                     'XDG_DATA_HOME': str(tmp_path / 'data')},
                                text=True, capture_output=True, timeout=20)
        assert list(work.iterdir()) == []
        return result
    return run


def test_powershell_prints_dns_array_and_does_not_reuse_workstation_identity(run_powershell, windows_fixture):
    result = run_powershell(windows_fixture)
    assert result.returncode == 0, result.stderr
    assert 'MANAGEMENT_DNS=["192.0.2.1"]' in result.stdout
    assert 'MANAGEMENT_GATEWAY=192.0.2.1' in result.stdout
    assert 'LOCAL_ADDRESS=192.0.2.20/24' in result.stdout
    assert 'MANAGEMENT_ADDRESS=<UNUSED_MAAS_SERVER_IPV4>/24' in result.stdout
    assert 'MANAGEMENT_INTERFACE_MAC=<MAAS_SERVER_ETHERNET_MAC>' in result.stdout
    assert 'MANAGEMENT_VLAN_ID=<MANAGEMENT_VLAN_FROM_SWITCH_UI>' in result.stdout


def test_powershell_requires_selection_with_multiple_defaults(run_powershell, windows_fixture):
    other = copy.deepcopy(windows_fixture['configurations'][0])
    other.update(InterfaceAlias='VPN', InterfaceIndex=3)
    windows_fixture['configurations'].append(other)
    windows_fixture['adapters'].append({'ifIndex': 3, 'Status': 'Up', 'MacAddress': ''})
    result = run_powershell(windows_fixture)
    assert result.returncode == 0, result.stderr
    assert 'Rerun with -InterfaceAlias' in result.stdout
    assert 'MANAGEMENT_GATEWAY=<CONFIRM_ROUTER_IPV4>' in result.stdout
    chosen = run_powershell(windows_fixture, 'Ethernet')
    assert chosen.returncode == 0, chosen.stderr
    assert 'LOCAL_INTERFACE=Ethernet' in chosen.stdout
    assert 'MANAGEMENT_GATEWAY=192.0.2.1' in chosen.stdout


@pytest.mark.parametrize('empty', [False, True])
def test_powershell_missing_ipv4_does_not_invent_settings(run_powershell, windows_fixture, empty):
    windows_fixture['configurations'][0].update(IPv4Address=None, IPv4DefaultGateway=None)
    if empty:
        windows_fixture = {'adapters': [], 'configurations': [], 'dns': []}
    result = run_powershell(windows_fixture)
    assert result.returncode == 0, result.stderr
    assert 'MANAGEMENT_GATEWAY=<CONFIRM_ROUTER_IPV4>' in result.stdout
    assert 'MANAGEMENT_DNS=<CONFIRM_IPV4_DNS_JSON_LIST>' in result.stdout
    assert 'LOCAL_ADDRESS=' not in result.stdout


def test_powershell_unknown_interface_fails(run_powershell, windows_fixture):
    result = run_powershell(windows_fixture, 'missing')
    assert result.returncode != 0
    assert 'not up with an IPv4 address' in result.stderr
