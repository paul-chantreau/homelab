"""Actions configuration must be complete before an installable ISO is built."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from maas_deploy.actions_config import catalog, resolve
from maas_deploy.autoinstall import render_autoinstall
from maas_deploy.config import ConfigError

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def variables(ready):
    document = {'network': ready.network, 'deployment': ready.deployment}
    result = {}
    for name, spec in catalog().items():
        if 'default' in spec:
            continue
        value = document
        for part in spec['path'].split('.'):
            value = value[part]
        if value is None:
            continue
        if spec['type'] == 'keys':
            value = '\n'.join(value)
        elif spec['type'].endswith('-list'):
            value = json.dumps(value)
        result[name] = str(value)
    return result


def test_defaults_are_applied_and_rendered(committed, variables):
    config, statuses = resolve(committed, variables)
    assert config.deployment['timezone'] == 'Europe/Paris'
    assert config.deployment['disk']['confirm_erase'] is True
    keyboard = yaml.safe_load(render_autoinstall(config.network, config.deployment, True))['autoinstall']['keyboard']
    assert keyboard == {'layout': 'fr', 'variant': 'legacy'}
    assert statuses['KEYBOARD_LAYOUT'] == 'default'


@pytest.mark.parametrize('name', [name for name, spec in catalog().items() if spec.get('required')])
@pytest.mark.parametrize('missing', ['', '   ', '__REQUIRED__'])
def test_every_required_variable_is_checked(committed, variables, name, missing):
    variables[name] = missing
    with pytest.raises(ConfigError, match=name):
        resolve(committed, variables)


@pytest.mark.parametrize('identifier', ['DISK_SERIAL', 'DISK_WWN', 'DISK_BY_ID'])
def test_each_disk_identifier_is_sufficient(committed, variables, identifier):
    for name in ['DISK_SERIAL', 'DISK_WWN', 'DISK_BY_ID']:
        variables.pop(name, None)
    variables[identifier] = '/dev/disk/by-id/wwn-example' if identifier == 'DISK_BY_ID' else 'EXAMPLE123'
    resolve(committed, variables)


def test_disk_placeholder_does_not_authorize_installation(committed, variables):
    variables['DISK_SERIAL'] = '__REQUIRED__'
    with pytest.raises(ConfigError, match='At least one'):
        resolve(committed, variables)


@pytest.mark.parametrize('name,value', [
    ('BACKUP_RETAIN', '0'), ('BACKUP_RETAIN', 'true'),
    ('MANAGEMENT_VLAN_ID', '1.0'), ('SWITCH_PROVISIONING_PORTS', '[true]'),
    ('SWITCH_PROVISIONING_PORTS', '[1]'), ('MANAGEMENT_DNS', '[]'),
    ('SSH_PUBLIC_KEY', '-----BEGIN OPENSSH PRIVATE KEY-----'),
    ('PROVISIONING_VLAN_ID', '1'), ('PROVISIONING_TEST_HOST', '198.51.100.110'),
])
def test_bad_types_and_inconsistent_config_are_rejected(committed, variables, name, value):
    variables[name] = value
    with pytest.raises(ConfigError):
        resolve(committed, variables)


def test_removed_options_cannot_override_fixed_settings(committed, variables):
    variables.update(DISK_MODEL='unexpected', PROVISIONING_GATEWAY='198.51.100.1',
                     PROVISIONING_DNS='["8.8.8.8"]', BACKUP_OFFHOST_TARGET='other:/backup',
                     CONFIRM_DISK_ERASE='false')
    config, _ = resolve(committed, variables)
    assert config.deployment['disk']['model'] is None
    assert config.deployment['backup']['offhost_target'] is None
    assert config.network['provisioning']['gateway'] is None
    assert config.network['provisioning']['dns'] == []


def test_cli_validates_before_writing_and_embeds_effective_config(tmp_path, variables):
    (tmp_path / 'config').mkdir()
    for name in ['network', 'deployment']:
        (tmp_path / 'config' / f'{name}.yaml').write_text((ROOT / 'config' / f'{name}.yaml').read_text())
    command = [sys.executable, str(ROOT / 'scripts/configure-actions.py'), '--root', str(tmp_path), '--write-config']
    env = {**os.environ, 'ACTIONS_VARIABLES_JSON': '{}'}
    original = (tmp_path / 'config/deployment.yaml').read_text()
    result = subprocess.run(command, env=env, capture_output=True, text=True)
    assert result.returncode == 1
    assert (tmp_path / 'config/deployment.yaml').read_text() == original
    env['ACTIONS_VARIABLES_JSON'] = json.dumps(variables)
    result = subprocess.run(command, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    saved = yaml.safe_load((tmp_path / 'config/deployment.yaml').read_text())['deployment']
    assert saved['disk']['serial'] == variables['DISK_SERIAL']
    assert saved['timezone'] == 'Europe/Paris'
    assert variables['SSH_PUBLIC_KEY'] not in result.stdout


def test_variable_creation_preserves_existing_and_creates_placeholders(monkeypatch):
    import runpy
    from types import SimpleNamespace
    module = runpy.run_path(str(ROOT / 'scripts/create-actions-variables.py'))
    calls = []
    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        if '--paginate' in command:
            return SimpleNamespace(returncode=0, stdout=json.dumps([
                {'variables': [{'name': 'SSH_PUBLIC_KEY'}]},
                {'variables': [{'name': 'HOSTNAME'}]},
            ]))
        return SimpleNamespace(returncode=0, stdout='')
    monkeypatch.setattr(subprocess, 'run', fake_run)
    monkeypatch.setattr(sys, 'argv', ['create-actions-variables.py'])
    assert module['main']() == 0
    payloads = [json.loads(kwargs['input']) for _, kwargs in calls[1:]]
    assert len(payloads) == len(catalog()) - 2
    assert not {'HOSTNAME', 'SSH_PUBLIC_KEY'} & {p['name'] for p in payloads}
    assert next(p['value'] for p in payloads if p['name'] == 'TIMEZONE') == 'Europe/Paris'
    assert next(p['value'] for p in payloads if p['name'] == 'DISK_SERIAL') == '__REQUIRED__'


def test_access_denial_never_attempts_variable_creation(monkeypatch):
    import runpy
    from types import SimpleNamespace
    module = runpy.run_path(str(ROOT / 'scripts/create-actions-variables.py'))
    calls = []
    def fake_run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=1, stdout='')
    monkeypatch.setattr(subprocess, 'run', fake_run)
    monkeypatch.setattr(sys, 'argv', ['create-actions-variables.py'])
    assert module['main']() == 1
    assert len(calls) == 1


def test_workflows_use_the_same_check_without_secret_bindings():
    for name in ['build-iso.yml', 'check-variables.yml']:
        workflow = yaml.safe_load((ROOT / '.github/workflows' / name).read_text())
        checks = [step for job in workflow['jobs'].values() for step in job['steps']
                  if step.get('uses') == './.github/actions/check-variables']
        assert len(checks) == 1
        assert checks[0]['with']['variables-json'] == '${{ toJSON(vars) }}'
        assert checks[0]['with'].get('write-config', 'false') == ('true' if name == 'build-iso.yml' else 'false')
