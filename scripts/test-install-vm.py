#!/usr/bin/env python3
"""Install the real Ubuntu ISO in an isolated disposable QEMU guest.

Uses synthetic network/disk identity and an ephemeral SSH key, never repository
Actions Secrets. Requires QEMU, qemu-img, xorriso, OVMF and the Python dev tools.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import shlex
import socket
import subprocess
import sys
import time
from urllib.parse import urlparse

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from maas_deploy.config import load_config
from maas_deploy.iso import build


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--iso', type=Path, required=True)
    parser.add_argument('--sums', type=Path, required=True)
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--ovmf-code', type=Path, required=True)
    parser.add_argument('--ovmf-vars', type=Path, required=True)
    parser.add_argument('--qemu', default='qemu-system-x86_64')
    parser.add_argument('--qemu-data')
    parser.add_argument('--accel', choices=['kvm', 'tcg'], default='kvm' if os.access('/dev/kvm', os.R_OK | os.W_OK) else 'tcg')
    parser.add_argument('--ssh-port', type=int, default=2222)
    parser.add_argument('--resume', action='store_true', help='Resume an existing test image; does not incorporate source changes')
    parser.add_argument('--timeout', type=int, default=3600)
    args = parser.parse_args()
    args.work.mkdir(parents=True, exist_ok=args.resume)
    work = args.work.resolve()
    if args.resume:
        key = work / 'test-key'
        config = load_config(work / 'source/config/network.yaml', work / 'source/config/deployment.yaml')
        image = work / 'test.iso'
        disk = work / 'disk.qcow2'
        variables = work / 'OVMF_VARS.fd'
    else:
        key = work / 'test-key'
        subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(key)], check=True)
        source = work / 'source'
        shutil.copytree(ROOT, source, ignore=shutil.ignore_patterns('.git', 'build', '__pycache__', '.pytest_cache'))
        config = load_config(source / 'config/network.yaml', source / 'config/deployment.yaml')
        config.network['management'].update(interface_mac='52:54:00:12:34:56', address='10.0.2.15/24', gateway='10.0.2.2', dns=['10.0.2.3'], vlan_id=1)
        config.network['provisioning'].update(vlan_id=100, subnet='192.0.2.0/24', controller_address='192.0.2.1')
        config.network['provisioning']['dhcp'].update(range_start='192.0.2.100', range_end='192.0.2.200')
        config.deployment['disk'].update(serial='HOMELAB-VM-TEST', wwn=None, by_id=None, model=None, confirm_erase=True)
        config.deployment['ssh_authorized_keys'] = [key.with_suffix('.pub').read_text().strip()]
        for name, value in [('network', config.network), ('deployment', config.deployment)]:
            (source / f'config/{name}.yaml').write_text(yaml.safe_dump({name: value}, sort_keys=False))
        template = source / 'autoinstall/autoinstall.yaml.j2'
        text = template.read_text()
        # Stream installer diagnostics to the VM's serial log. This service
        # exists only in the live test guest, never on the installed controller.
        journal = ['systemd-run', '--unit=homelab-test-journal', '/bin/sh', '-c',
                   'exec journalctl -f -n 200 -o short-iso > /dev/ttyS0']
        text = text.replace('  early-commands:', '  early-commands:\n    - ' + json.dumps(journal))
        # Local cloud runners may need an egress proxy. Never embed proxy credentials.
        proxy = os.environ.get('HTTPS_PROXY') or os.environ.get('https_proxy')
        if proxy:
            parsed = urlparse(proxy)
            if parsed.username or parsed.password:
                raise ValueError('VM test cannot embed proxy credentials')
            if parsed.scheme == 'http' and parsed.hostname:
                # Container-only names (such as "proxy") may not resolve in
                # the guest. HTTP proxies do not require a TLS hostname.
                address = socket.gethostbyname(parsed.hostname)
                proxy = parsed._replace(netloc=address + (f':{parsed.port}' if parsed.port else '')).geturl()
            text = text.replace('  version: 1', '  version: 1\n  proxy: ' + json.dumps(proxy))
            text = text.replace('  late-commands:', '''  late-commands:
        - mkdir -p /target/etc/systemd/system/snapd.service.d
        - cp /cdrom/maas-deploy/systemd/test-proxy.conf /target/etc/systemd/system/snapd.service.d/proxy.conf''')
            (source / 'systemd/test-proxy.conf').write_text('[Service]\nEnvironment="HTTP_PROXY=' + proxy + '" "HTTPS_PROXY=' + proxy + '"\n')
        template.write_text(text)
        image = work / 'test.iso'
        build(source, args.iso, image, config, args.sums.read_text())
        disk = work / 'disk.qcow2'
        subprocess.run(['qemu-img', 'create', '-f', 'qcow2', str(disk), '40G'], check=True)
        variables = work / 'OVMF_VARS.fd'
        shutil.copyfile(args.ovmf_vars, variables)
    command = [args.qemu, '-machine', f'q35,accel={args.accel}', '-cpu', 'host' if args.accel == 'kvm' else 'max',
               '-m', '8192', '-smp', '2', '-display', 'none', '-serial', f'file:{work / "serial.log"}',
               '-monitor', f'unix:{work / "monitor.sock"},server=on,wait=off',
               '-drive', f'if=pflash,format=raw,readonly=on,file={args.ovmf_code}',
               '-drive', f'if=pflash,format=raw,file={variables}',
               '-drive', f'file={disk},format=qcow2,if=none,id=disk0',
               '-device', 'virtio-blk-pci,drive=disk0,serial=HOMELAB-VM-TEST',
               '-cdrom', str(image), '-boot', 'order=c,once=d',
               '-nic', f'user,model=virtio-net-pci,mac=52:54:00:12:34:56,hostfwd=tcp:127.0.0.1:{args.ssh_port}-:22']
    if args.qemu_data:
        command.extend(['-L', args.qemu_data])
    ssh = ['ssh', '-i', str(key), '-p', str(args.ssh_port), '-o', 'StrictHostKeyChecking=no',
           '-o', f'UserKnownHostsFile={work / "known_hosts"}', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5',
           f'{config.deployment["admin_user"]}@127.0.0.1']
    with (work / 'qemu.log').open('w') as log:
        (work / 'serial.log').unlink(missing_ok=True)
        process = subprocess.Popen(command, stdout=log, stderr=log)
        started = time.monotonic()
        try:
            while time.monotonic() - started < args.timeout:
                if process.poll() is not None:
                    raise RuntimeError('QEMU exited; see qemu.log')
                serial = work / 'serial.log'
                if serial.exists():
                    with serial.open('rb') as output:
                        output.seek(max(0, serial.stat().st_size - 128 * 1024))
                        lines = output.read().decode(errors='replace').splitlines()
                    if any('subiquity/Install/' in line and ': FAIL:' in line for line in lines):
                        raise RuntimeError('Ubuntu installer failed; see serial.log and screen.png')
                probe = subprocess.run([*ssh, 'sudo -n test -f /var/lib/maas-provision/complete'], capture_output=True, text=True)
                if probe.returncode == 0:
                    break
                failed = subprocess.run([*ssh, 'sudo -n systemctl is-failed --quiet maas-firstboot.service'], capture_output=True)
                if failed.returncode == 0:
                    raise RuntimeError('MAAS first boot failed; see first-boot.log and ansible.log')
                print(f'Waiting for installed MAAS: {int(time.monotonic() - started)}s; diagnostics in {work}', flush=True)
                time.sleep(20)
            else:
                raise TimeoutError('Full install did not complete; retained VM and logs for diagnosis')
            # Production commands are invoked through their installed symlinks.
            check = subprocess.run([*ssh, 'sudo -n maas-healthcheck && sudo -n maas-backup'], capture_output=True, text=True)
            (work / 'verification.log').write_text(check.stdout + check.stderr)
            check.check_returncode()
            archive = check.stdout.strip().splitlines()[-1]
            if not archive.startswith(config.deployment['backup']['directory'].rstrip('/') + '/') or not archive.endswith('.dump'):
                raise RuntimeError('Backup did not report its completed archive path')
            restored = subprocess.run([*ssh, 'sudo -n maas-restore --confirm RESTORE ' + shlex.quote(archive) + ' && sudo -n maas-healthcheck'], capture_output=True, text=True)
            (work / 'restore.log').write_text(restored.stdout + restored.stderr)
            restored.check_returncode()
            vlan_id = config.network['provisioning']['vlan_id']
            stop_phrase = shlex.quote(f'DEACTIVATE DHCP ON VLAN {vlan_id}')
            stopped = subprocess.run([*ssh, f"printf '%s\\n' {stop_phrase} | sudo -n maas-network-deactivate && sudo -n maas-healthcheck"], capture_output=True, text=True)
            (work / 'dhcp-stop.log').write_text(stopped.stdout + stopped.stderr)
            stopped.check_returncode()
            rerun = subprocess.run([*ssh, 'cd /opt/maas-deploy/ansible && sudo -n ansible-playbook install.yml && sudo -n maas-healthcheck'], capture_output=True, text=True)
            (work / 'rerun.log').write_text(rerun.stdout + rerun.stderr)
            rerun.check_returncode()
            start_phrase = shlex.quote(f'ACTIVATE DHCP ON VLAN {vlan_id}')
            resumed = subprocess.run([*ssh, f"printf '%s\\n' {start_phrase} | sudo -n maas-network-activate && sudo -n maas-healthcheck"], capture_output=True, text=True)
            (work / 'dhcp-resume.log').write_text(resumed.stdout + resumed.stderr)
            resumed.check_returncode()
            # Verify that a reboot retains dependencies/config and service readiness.
            subprocess.run([*ssh, 'sudo -n reboot'], capture_output=True)
            time.sleep(20)
            for _ in range(30):
                check = subprocess.run([*ssh, 'sudo -n maas-healthcheck'], capture_output=True, text=True)
                if check.returncode == 0:
                    break
                time.sleep(10)
            else:
                raise RuntimeError('MAAS did not recover after reboot')
            (work / 'result.json').write_text(json.dumps({'passed': True, 'acceleration': args.accel,
                                                         'checks': ['UEFI install', 'automatic provisioning DHCP', 'MAAS API and VLAN health', 'private backup', 'database restore', 'DHCP stop persists through Ansible rerun', 'manual DHCP resume', 'reboot']}, indent=2))
            print('Full installation, command symlinks, backup, restore, rerun and reboot checks passed.', flush=True)
        finally:
            try:
                with socket.socket(socket.AF_UNIX) as monitor:
                    monitor.settimeout(1)
                    monitor.connect(str(work / 'monitor.sock'))
                    monitor.recv(4096)
                    monitor.sendall(f'screendump {work / "screen.png"} -f png\n'.encode())
                    while monitor.recv(4096):
                        pass
            except (OSError, TimeoutError):
                pass
            for name, remote in [('first-boot', '/var/log/maas-provision/first-boot.log'),
                                 ('ansible', '/var/log/maas-provision/ansible.log'),
                                 ('cloud-init', '/var/log/cloud-init-output.log')]:
                try:
                    diagnostic = subprocess.run([*ssh, f'sudo -n cat {remote}'], capture_output=True, text=True, timeout=15)
                    if diagnostic.returncode == 0:
                        (work / f'{name}.log').write_text(diagnostic.stdout)
                except subprocess.TimeoutExpired:
                    pass
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
