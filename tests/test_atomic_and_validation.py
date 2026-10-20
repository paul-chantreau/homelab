"""Regression coverage for durable credentials and semantic input validation."""
import os
from pathlib import Path
import subprocess

import pytest

from maas_deploy.config import ConfigError, INSTALL_MODE, validate
from maas_deploy.secrets import ensure, load, store_api_key


def test_failed_replace_does_not_destroy_database_password(tmp_path, monkeypatch):
    path = tmp_path / 'secrets.env'
    ensure(path)
    before = path.read_bytes()
    def fail(*args):
        raise OSError('simulated interrupted replacement')
    monkeypatch.setattr('maas_deploy.files.os.replace', fail)
    with pytest.raises(OSError):
        store_api_key(path, 'new:key:value')
    assert path.read_bytes() == before
    assert load(path)['POSTGRES_PASSWORD']
    assert not list(tmp_path.glob('.secrets.env.*.partial'))


@pytest.mark.parametrize('field,value', [('timezone', 'Mars/Olympus'),
                                        ('keyboard_variant', 'does-not-exist'),
                                        ('locale', 'xx_YY.UTF-8'),
                                        ('ssh_authorized_keys', ['ssh-ed25519 AAAA'])])
def test_unusable_settings_fail_before_build(ready, field, value):
    ready.deployment[field] = value
    with pytest.raises(ConfigError):
        validate(ready, INSTALL_MODE)


def test_malformed_ubuntu_settings_report_a_configuration_error(ready):
    ready.deployment['ubuntu'] = ['not', 'a', 'mapping']
    with pytest.raises(ConfigError, match='deployment.ubuntu'):
        validate(ready, INSTALL_MODE)


def test_installed_activation_symlink_finds_its_checkout(tmp_path):
    root = Path(__file__).resolve().parents[1]
    command = tmp_path / 'sbin/maas-network-activate'
    command.parent.mkdir()
    command.symlink_to(root / 'scripts/activate-dhcp.sh')
    bindir = tmp_path / 'bin'
    bindir.mkdir()
    identity = bindir / 'id'
    identity.write_text('#!/bin/sh\necho 0\n')
    identity.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if k != 'MAAS_DEPLOY_ROOT'}
    env['PATH'] = str(bindir) + ':' + env['PATH']
    result = subprocess.run(['bash', str(command)], env=env, capture_output=True, text=True)
    assert result.returncode != 0  # canonical placeholders are deliberately incomplete
    assert 'required before installation' in result.stderr
    assert 'No such file or directory' not in result.stderr
