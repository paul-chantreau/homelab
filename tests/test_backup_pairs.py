"""A failed configuration bundle must not consume a completed backup slot."""
from pathlib import Path
import stat
import tarfile

import pytest

from maas_deploy.backup import backup_controller


def test_failed_bundle_preserves_completed_backups(ready, tmp_path, monkeypatch):
    directory = tmp_path / 'backups'
    directory.mkdir()
    old = directory / 'maas-20260101.dump'
    old.write_bytes(b'previous archive')
    companion = old.with_suffix('.config.tar.gz')
    companion.write_bytes(b'previous configuration')
    ready.deployment['backup'].update(directory=str(directory), retain=1)
    monkeypatch.setattr('maas_deploy.backup.create_backup', lambda db, path, **kw: path.write_bytes(b'new archive'))
    with pytest.raises(FileNotFoundError):
        backup_controller(ready, tmp_path / 'missing-config')
    assert old.read_bytes() == b'previous archive'
    assert companion.read_bytes() == b'previous configuration'
    assert sorted(path.name for path in directory.iterdir()) == sorted([old.name, companion.name])


def test_success_publishes_private_pair_and_rotates_only_completed_pairs(ready, tmp_path, monkeypatch):
    directory = tmp_path / 'backups'
    directory.mkdir()
    old = directory / 'maas-20260101.dump'
    old.write_bytes(b'old archive')
    companion = old.with_suffix('.config.tar.gz')
    companion.write_bytes(b'old configuration')
    orphan = directory / 'maas-20260102.dump'
    orphan.write_bytes(b'unpaired archive')
    ready.deployment['backup'].update(directory=str(directory), retain=1)
    (tmp_path / 'config').mkdir()
    for name in ['network.yaml', 'deployment.yaml']:
        (tmp_path / 'config' / name).write_text('synthetic: true\n')
    secret = tmp_path / 'secrets.env'
    secret.write_text('SYNTHETIC=value\n')
    original = tarfile.TarFile.add
    def add(self, source, **kwargs):
        return original(self, secret if Path(source) == Path('/etc/maas-provision/secrets.env') else source, **kwargs)
    monkeypatch.setattr(tarfile.TarFile, 'add', add)
    def dump(db, path, **kwargs):
        path.write_bytes(b'new archive')
        path.chmod(0o600)
    monkeypatch.setattr('maas_deploy.backup.create_backup', dump)
    published = backup_controller(ready, tmp_path)
    bundle = published.with_suffix('.config.tar.gz')
    assert published.is_file() and bundle.is_file()
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in [published, bundle])
    with tarfile.open(bundle) as archive:
        assert set(archive.getnames()) == {'network.yaml', 'deployment.yaml', 'secrets.env'}
    assert not old.exists() and not companion.exists()
    assert orphan.exists()
    assert not list(directory.glob('.*.pending'))
