"""Stable identifiers gate every supported runtime Subiquity selector."""
from pathlib import Path
import runpy
import sys

import pytest
import yaml

from maas_deploy.autoinstall import REFUSAL_SERIAL, render_autoinstall
from maas_deploy.diskmatch import bind_verified_disk, matching_disks


@pytest.fixture(params=['wrapped', 'extracted'])
def installer(tmp_path, ready, request):
    path = tmp_path / 'autoinstall.yaml'
    document = yaml.safe_load(render_autoinstall(ready.network, ready.deployment, True))
    if request.param == 'extracted':
        document = document['autoinstall']
    path.write_text(yaml.safe_dump(document))
    return path


@pytest.mark.parametrize('identity', [{'serial': 'SHORT'}, {'wwn': 'eui.1234'},
                                       {'by_id': '/dev/disk/by-id/nvme-example'}])
def test_all_identifiers_resolve_to_the_same_exact_device(installer, identity):
    devices = [
        {'name': 'sda', 'type': 'disk', 'serial': 'USB', 'install_media': True},
        {'name': 'nvme0n1', 'type': 'disk', 'serial': 'SHORT', 'wwn': 'eui.1234',
         'by_id_paths': ['/dev/disk/by-id/nvme-example']},
        {'name': 'nvme1n1', 'type': 'disk', 'serial': 'OTHER', 'wwn': 'eui.5678'},
    ]
    matches = matching_disks(devices, identity)
    assert [d['name'] for d in matches] == ['nvme0n1']
    bind_verified_disk(installer, matches[0])
    document = yaml.safe_load(installer.read_text())
    assert document.get('autoinstall', document)['storage']['layout']['match'] == {'path': '/dev/nvme0n1'}
    before = installer.read_text()
    bind_verified_disk(installer, matches[0])
    assert installer.read_text() == before


@pytest.mark.parametrize('content', ['null', '[]', '{}', 'autoinstall: null',
                                    'storage: {}', 'storage: {layout: {match: []}}',
                                    'storage: {layout: {match: {path: /dev/other}}}', '[bad'])
def test_invalid_installer_document_never_changes(tmp_path, content):
    path = tmp_path / 'autoinstall.yaml'
    path.write_text(content)
    with pytest.raises(ValueError):
        bind_verified_disk(path, {'name': 'nvme0n1'})
    assert path.read_text() == content


@pytest.mark.parametrize('disk_count', [0, 1, 2])
def test_preinstall_checks_unique_disk_before_rewriting(installer, ready, tmp_path, monkeypatch, disk_count):
    root = Path(__file__).resolve().parents[1]
    main = runpy.run_path(str(root / 'scripts/preinstall-check.py'))['main']
    network = tmp_path / 'network.yaml'
    deployment = tmp_path / 'deployment.yaml'
    network.write_text(yaml.safe_dump({'network': ready.network}))
    deployment.write_text(yaml.safe_dump({'deployment': ready.deployment}))
    devices = [{'name': f'nvme{i}n1', 'type': 'disk', 'serial': 'DISKSERIAL1'} for i in range(disk_count)]
    monkeypatch.setitem(main.__globals__, '_disks', lambda: devices)
    monkeypatch.setattr(sys, 'argv', ['preinstall-check.py', '--network', str(network),
                                     '--deployment', str(deployment), '--autoinstall', str(installer)])
    before = installer.read_text()
    assert main() == (0 if disk_count == 1 else 1)
    document = yaml.safe_load(installer.read_text())
    if disk_count == 1:
        assert document.get('autoinstall', document)['storage']['layout']['match'] == {'path': '/dev/nvme0n1'}
    else:
        assert installer.read_text() == before


def test_mounted_disk_and_installation_media_are_never_selected():
    for flags in [{'mounted': True}, {'install_media': True}]:
        assert matching_disks([{'name': 'sda', 'type': 'disk', 'serial': 'MATCH', **flags}], {'serial': 'MATCH'}) == []


def test_rendered_configuration_cannot_select_a_disk_without_early_check(ready):
    doc = yaml.safe_load(render_autoinstall(ready.network, ready.deployment, True))['autoinstall']
    assert doc['storage']['layout']['match'] == {'serial': REFUSAL_SERIAL}
    assert '--autoinstall /autoinstall.yaml' in doc['early-commands'][0]
