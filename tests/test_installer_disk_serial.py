"""Keep physical serial checks while translating to Subiquity's udev identity."""
from types import SimpleNamespace

import pytest
import yaml

from maas_deploy.autoinstall import render_autoinstall
from maas_deploy.diskmatch import update_installer_serial


@pytest.fixture
def installer(tmp_path, ready):
    path = tmp_path / 'autoinstall.yaml'
    path.write_text(render_autoinstall(ready.network, ready.deployment, True))
    return path


@pytest.mark.parametrize('udev_serial', ['VENDOR_MODEL_EXAMPLE123_1', 'EXAMPLE123'])
def test_verified_disk_uses_full_udev_identity(installer, ready, monkeypatch, udev_serial):
    # Use synthetic identities, never real machine identifiers in the repository.
    document = yaml.safe_load(installer.read_text())
    document['autoinstall']['storage']['layout']['match']['serial'] = 'EXAMPLE123'
    installer.write_text(yaml.safe_dump(document))
    def run(command, **kwargs):
        assert command == ['udevadm', 'info', '--query=property', '--name=/dev/nvme0n1']
        return SimpleNamespace(stdout=f'ID_SERIAL_SHORT=EXAMPLE123\nID_SERIAL={udev_serial}\n')
    monkeypatch.setattr('maas_deploy.diskmatch.subprocess.run', run)
    update_installer_serial(installer, {'name': 'nvme0n1'}, {'serial': 'EXAMPLE123'})
    after = yaml.safe_load(installer.read_text())
    assert after['autoinstall']['storage']['layout']['match'] == {'serial': udev_serial}
    document['autoinstall']['storage']['layout']['match']['serial'] = udev_serial
    assert after == document


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
    early = yaml.safe_load(installer.read_text())['autoinstall']['early-commands']
    assert '--autoinstall /autoinstall.yaml' in early[0]


def test_non_serial_selection_is_not_changed(installer):
    before = installer.read_text()
    update_installer_serial(installer, {'name': 'nvme0n1'}, {'wwn': 'eui.example'})
    assert installer.read_text() == before
