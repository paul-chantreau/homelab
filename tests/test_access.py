"""Initial login details reach a terminal without appearing in ordinary output."""

import os
from pathlib import Path
import select
import shlex
import subprocess
import sys

import pytest
import yaml

from maas_deploy import cli
from maas_deploy.access import display_access
from maas_deploy.secrets import ensure, load


@pytest.fixture
def terminal():
    master, slave = os.openpty()
    try:
        yield master, Path(os.ttyname(slave))
    finally:
        os.close(slave)
        os.close(master)


def test_console_shows_management_url_and_maas_credentials(ready, tmp_path, terminal, capsys):
    secrets_path = tmp_path / 'secrets.env'
    ensure(secrets_path)
    values = load(secrets_path)
    ready.deployment['maas']['admin_username'] = 'labadmin'
    master, console_path = terminal

    display_access(ready, secrets_path, console_path)

    assert select.select([master], [], [], 1)[0]
    message = os.read(master, 4096).decode().replace('\r\n', '\n')
    assert 'Access MAAS with the following link:\nhttp://192.0.2.10:5240/MAAS/' in message
    assert 'Username: labadmin\n' in message
    assert f"Password: {values['MAAS_ADMIN_PASSWORD']}\n" in message
    assert ready.network['provisioning']['controller_address'] not in message
    assert values['POSTGRES_PASSWORD'] not in message
    assert '/24' not in message
    captured = capsys.readouterr()
    assert captured.out == captured.err == ''


def test_regular_file_is_not_used_as_a_console(ready, tmp_path, capsys):
    destination = tmp_path / 'log'
    destination.write_text('existing log\n')
    with pytest.raises(OSError, match='require a terminal'):
        display_access(ready, tmp_path / 'unused-secrets.env', destination)
    assert destination.read_text() == 'existing log\n'
    assert capsys.readouterr().out == ''


def test_missing_console_is_not_created(ready, tmp_path):
    destination = tmp_path / 'missing-console'
    with pytest.raises(FileNotFoundError):
        display_access(ready, tmp_path / 'unused-secrets.env', destination)
    assert not destination.exists()


def test_insecure_secrets_file_is_not_displayed(ready, tmp_path, terminal, capsys):
    secrets_path = tmp_path / 'secrets.env'
    ensure(secrets_path)
    secrets_path.chmod(0o644)
    master, console_path = terminal
    with pytest.raises(PermissionError):
        display_access(ready, secrets_path, console_path)
    assert not select.select([master], [], [], 0)[0]
    captured = capsys.readouterr()
    assert captured.out == captured.err == ''


def test_cli_uses_installed_configuration_and_local_credentials(ready, monkeypatch):
    calls = []
    monkeypatch.setattr(cli, '_config', lambda: ready)
    monkeypatch.setattr(cli, 'display_access', lambda *args: calls.append(args))
    assert cli.main(['console-access']) == 0
    assert calls == [(ready, Path('/etc/maas-provision/secrets.env'))]


@pytest.mark.parametrize('console_available', [True, False])
def test_first_boot_keeps_credentials_out_of_logs(ready, tmp_path, terminal, console_available):
    source = Path(__file__).resolve().parents[1]
    root = tmp_path / 'installation'
    (root / 'ansible').mkdir(parents=True)
    (root / 'maas_deploy').symlink_to(source / 'maas_deploy', target_is_directory=True)
    (root / 'config').mkdir()
    (root / 'config/network.yaml').write_text(yaml.safe_dump({'network': ready.network}))
    (root / 'config/deployment.yaml').write_text(yaml.safe_dump({'deployment': ready.deployment}))
    secrets_path = tmp_path / 'secrets.env'
    ensure(secrets_path)
    values = load(secrets_path)
    master, terminal_path = terminal
    console_path = terminal_path if console_available else tmp_path / 'missing-console'
    harness = tmp_path / 'console-command.py'
    harness.write_text(
        'import sys\nfrom pathlib import Path\nfrom maas_deploy import access, cli\n'
        f'cli.display_access = lambda config, path: access.display_access(config, Path({str(secrets_path)!r}), Path({str(console_path)!r}))\n'
        'assert sys.argv[1:3] == ["-m", "maas_deploy"]\n'
        'raise SystemExit(cli.main(sys.argv[3:]))\n'
    )
    bindir = tmp_path / 'bin'
    bindir.mkdir()
    (bindir / 'ansible-playbook').write_text('#!/bin/sh\necho "Synthetic Ansible setup completed."\n')
    (bindir / 'ansible-playbook').chmod(0o755)
    (bindir / 'python3').write_text(
        f'#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(str(harness))} "$@"\n'
    )
    (bindir / 'python3').chmod(0o755)
    state = tmp_path / 'state'
    logs = tmp_path / 'logs'
    env = {**os.environ, 'PATH': str(bindir) + ':' + os.environ['PATH'],
           'MAAS_DEPLOY_ROOT': str(root), 'MAAS_STATE_DIR': str(state), 'MAAS_LOG_DIR': str(logs)}

    result = subprocess.run(['bash', str(source / 'scripts/first-boot.sh')], env=env,
                            capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    assert (state / 'complete').exists()
    log = (logs / 'first-boot.log').read_text()
    assert 'Synthetic Ansible setup completed.' in log
    for value in values.values():
        assert value not in log + result.stdout + result.stderr
    if console_available:
        assert select.select([master], [], [], 1)[0]
        message = os.read(master, 4096).decode()
        assert 'http://192.0.2.10:5240/MAAS/' in message
        assert f"Password: {values['MAAS_ADMIN_PASSWORD']}" in message
    else:
        assert 'could not be displayed on tty1' in log
        assert not select.select([master], [], [], 0)[0]
