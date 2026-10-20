"""Validate real Ubuntu OpenSSH before socket activation creates /run/sshd."""

import os
from pathlib import Path
import subprocess
import uuid

import pytest

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope='module')
def ssh_container():
    if os.environ.get('HOMELAB_TEST_SSH') != '1':
        pytest.skip('set HOMELAB_TEST_SSH=1 to test OpenSSH in an isolated Ubuntu container')
    name = 'homelab-ssh-test-' + uuid.uuid4().hex[:10]
    subprocess.run(['docker', 'run', '-d', '--rm', '--name', name, 'ubuntu:24.04', 'sleep', 'infinity'],
                   check=True, capture_output=True, text=True, timeout=180)
    prefix = ['docker', 'exec', '-i', name]

    def run(*command, check=True, input=None):
        return subprocess.run([*prefix, *command], check=check, input=input,
                              capture_output=True, text=True, timeout=300)

    try:
        run('sh', '-c', 'printf "#!/bin/sh\\nexit 101\\n" > /usr/sbin/policy-rc.d && chmod 755 /usr/sbin/policy-rc.d')
        run('apt-get', '-o', 'Acquire::Retries=2', '-o', 'Acquire::http::Timeout=30', 'update')
        run('env', 'DEBIAN_FRONTEND=noninteractive', 'apt-get', 'install', '-y', '--no-install-recommends',
            'ansible-core', 'openssh-server')
        run('ssh-keygen', '-A')
        run('mkdir', '-p', '/work')
        subprocess.run(['docker', 'cp', str(ROOT / 'ansible/roles/security'), f'{name}:/work/security'],
                       check=True, capture_output=True, text=True)
        playbook = '''---
- name: Exercise the production SSH configuration tasks
  hosts: localhost
  connection: local
  gather_facts: false
  tasks:
    - name: Apply the security tasks
      ansible.builtin.import_tasks: /work/security/tasks/main.yml
  handlers:
    - name: Restart ssh
      ansible.builtin.debug:
        msg: Configuration validated without starting a network service
'''
        run('tee', '/work/security-test.yml', input=playbook)
        yield run
    finally:
        subprocess.run(['docker', 'rm', '-f', name], check=False, capture_output=True, text=True)


def test_security_role_handles_missing_sshd_directory(ssh_container):
    run = ssh_container
    run('rm', '-rf', '/run/sshd')
    before = run('/usr/sbin/sshd', '-t', check=False)
    assert before.returncode != 0
    assert 'Missing privilege separation directory: /run/sshd' in before.stderr

    run('ansible-playbook', '-i', 'localhost,', '/work/security-test.yml')
    run('/usr/sbin/sshd', '-t')
    assert run('stat', '-c', '%u:%g:%a', '/run/sshd').stdout.strip() == '0:0:755'
    effective = run('/usr/sbin/sshd', '-T').stdout.splitlines()
    assert 'passwordauthentication no' in effective
    assert 'kbdinteractiveauthentication no' in effective
    assert 'permitrootlogin no' in effective

    repeated = run('ansible-playbook', '-i', 'localhost,', '/work/security-test.yml')
    assert 'changed=0' in repeated.stdout
