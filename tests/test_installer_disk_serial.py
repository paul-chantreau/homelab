"""Keep physical serial checks while translating to Subiquity's udev identity."""
from types import SimpleNamespace

import pytest
import yaml

from maas_deploy.autoinstall import render_autoinstall
from maas_deploy.diskmatch import update_installer_serial


@pytest.fixture(params=['wrapped', 'unwrapped'])
def installer(tmp_path, ready, request):
    path = tmp_path / 'autoinstall.yaml'
    document = yaml.safe_load(render_autoinstall(ready.network, ready.deployment, True))
    if request.param == 'unwrapped':
        document = document['autoinstall']
    path.write_text(yaml.safe_dump(document, sort_keys=False))
    return path


@pytest.mark.parametrize('udev_serial', ['VENDOR_MODEL_EXAMPLE123_1', 'EXAMPLE123'])
def test_verified_disk_uses_full_udev_identity(installer, ready, monkeypatch, udev_serial):
    # Use synthetic identities, never real machine identifiers in the repository.
    document = yaml.safe_load(installer.read_text())
    config = document.get('autoinstall', document)
    config['storage']['layout']['match']['serial'] = 'EXAMPLE123'
    installer.write_text(yaml.safe_dump(document))
    def run(command, **kwargs):
        assert command == ['udevadm', 'info', '--query=property', '--name=/dev/nvme0n1']
        return SimpleNamespace(stdout=f'ID_SERIAL_SHORT=EXAMPLE123\nID_SERIAL={udev_serial}\n')
    monkeypatch.setattr('maas_deploy.diskmatch.subprocess.run', run)
    update_installer_serial(installer, {'name': 'nvme0n1'}, {'serial': 'EXAMPLE123'})
    after = yaml.safe_load(installer.read_text())
    assert after.get('autoinstall', after)['storage']['layout']['match'] == {'serial': udev_serial}
    config['storage']['layout']['match']['serial'] = udev_serial
    assert after == document
    # Retrying early-commands must preserve an already resolved selector.
    update_installer_serial(installer, {'name': 'nvme0n1'}, {'serial': 'EXAMPLE123'})
    assert yaml.safe_load(installer.read_text()) == after


@pytest.mark.parametrize('properties', [
    'ID_SERIAL_SHORT=DISKSERIAL1\n',
    'ID_SERIAL=MODEL_OTHER\nID_SERIAL_SHORT=OTHER\n',
])
def test_unresolved_or_inconsistent_udev_identity_does_not_rewrite(installer, monkeypatch, properties):
    before = installer.read_text()
    monkeypatch.setattr('maas_deploy.diskmatch.subprocess.run', lambda *a, **k: SimpleNamespace(stdout=properties))
    with pytest.raises(ValueError):
        update_installer_serial(installer, {'name': 'nvme0n1'}, {'serial': 'DISKSERIAL1'})
    assert installer.read_text() == before


def test_installer_selector_must_match_verified_configuration(installer, monkeypatch):
    before = installer.read_text()
    monkeypatch.setattr('maas_deploy.diskmatch.subprocess.run', lambda *a, **k: SimpleNamespace(stdout='ID_SERIAL=MODEL_OTHER\nID_SERIAL_SHORT=OTHER\n'))
    with pytest.raises(ValueError, match='selector differs'):
        update_installer_serial(installer, {'name': 'nvme0n1'}, {'serial': 'OTHER'})
    assert installer.read_text() == before


def test_early_command_resolves_before_subiquity_storage_selection(installer):
    document = yaml.safe_load(installer.read_text())
    early = document.get('autoinstall', document)['early-commands']
    assert '--autoinstall /autoinstall.yaml' in early[0]


def test_non_serial_selection_is_not_changed(installer):
    before = installer.read_text()
    update_installer_serial(installer, {'name': 'nvme0n1'}, {'wwn': 'eui.example'})
    assert installer.read_text() == before


@pytest.mark.parametrize('content', ['null', '[]', '{}', 'autoinstall: null',
                                     'storage: {}', 'storage: {layout: {match: []}}', '[bad'])
def test_invalid_installer_document_fails_without_writing(tmp_path, monkeypatch, content):
    path = tmp_path / 'autoinstall.yaml'
    path.write_text(content)
    monkeypatch.setattr('maas_deploy.diskmatch.subprocess.run', lambda *a, **k: SimpleNamespace(stdout='ID_SERIAL=FULL_SERIAL\nID_SERIAL_SHORT=SHORT\n'))
    with pytest.raises(ValueError):
        update_installer_serial(path, {'name': 'nvme0n1'}, {'serial': 'SHORT'})
    assert path.read_text() == content


@pytest.mark.parametrize('disk_count', [0, 1, 2])
def test_preinstall_checks_unique_disk_before_rewriting(installer, ready, tmp_path, monkeypatch, disk_count):
    import runpy
    import sys
    from pathlib import Path
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
    monkeypatch.setattr('maas_deploy.diskmatch.subprocess.run', lambda *a, **k: SimpleNamespace(stdout='ID_SERIAL=MODEL_DISKSERIAL1_1\nID_SERIAL_SHORT=DISKSERIAL1\n'))
    before = installer.read_text()
    assert main() == (0 if disk_count == 1 else 1)
    if disk_count != 1:
        assert installer.read_text() == before
    else:
        document = yaml.safe_load(installer.read_text())
        assert document.get('autoinstall', document)['storage']['layout']['match']['serial'] == 'MODEL_DISKSERIAL1_1'
