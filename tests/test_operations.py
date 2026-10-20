"""Disk matching, discovery, backups, secrets, and first-boot restart behavior."""

import os
import stat
import subprocess
import textwrap
from pathlib import Path

from maas_deploy.backup import select_expired
from maas_deploy.discovery import assemble, summary
from maas_deploy.diskmatch import matching_disks
from maas_deploy.grub import AUTOINSTALL_ARG, patch_grub
from maas_deploy.postgres_setup import merge_hba, plan
from maas_deploy.secrets import ensure, load

ROOT = Path(__file__).resolve().parents[1]


def test_disk_match_requires_one_stable_identifier():
    devices = [
        {"name": "sda", "type": "disk", "serial": "OTHER", "wwn": "", "model": "Big", "by_id_paths": []},
        {"name": "sdb", "type": "disk", "serial": "DISKSERIAL1", "wwn": "0xabc", "model": "Small", "by_id_paths": ["/dev/disk/by-id/ata-small"]},
    ]
    assert matching_disks(devices, {"serial": None, "wwn": None, "by_id": None, "model": "Big"}) == []
    assert [item["name"] for item in matching_disks(devices, {"serial": "diskserial1"})] == ["sdb"]
    assert [item["name"] for item in matching_disks(devices, {"wwn": "abc"})] == ["sdb"]
    assert matching_disks(devices, {"serial": "DISKSERIAL1", "model": "big"}) == []


def test_discovery_does_not_claim_switch_visibility():
    report = assemble(
        [{"ifname": "eth0", "ifindex": 2, "addr_info": [{"family": "inet", "local": "192.0.2.20", "prefixlen": 24}]}],
        [{"ifindex": 2, "ifname": "eth0", "address": "52:54:00:00:00:01", "operstate": "UP"}],
        [{"dst": "default", "gateway": "192.0.2.1", "dev": "eth0"}],
    )
    text = summary(report)
    assert "does not identify Netgear" in text
    assert report["interfaces"][0]["addresses"] == ["192.0.2.20/24"]


def test_postgres_plan_is_idempotent():
    first = plan({"role_exists": False, "database_exists": False, "hba": "local all all peer\n"}, "maas", "maas", "secret")
    assert first["role_sql"].startswith('CREATE ROLE "maas"')
    assert "secret" in first["role_sql"]
    second = plan({"role_exists": True, "database_exists": True, "hba": first["hba"]}, "maas", "maas", "secret")
    assert second["role_sql"] is None
    assert second["database_sql"] is None
    assert merge_hba(first["hba"], "maas", "maas") == first["hba"]


def test_backup_retention_keeps_the_newest():
    paths = [Path(f"maas-2026010{index}T000000Z.dump") for index in range(1, 5)]
    expired = select_expired(paths, 2)
    assert [path.name for path in expired] == ["maas-20260101T000000Z.dump", "maas-20260102T000000Z.dump"]


def test_secrets_survive_a_second_call(tmp_path):
    path = tmp_path / "secrets.env"
    assert ensure(path) == "created"
    first = load(path)
    assert ensure(path) == "exists"
    assert load(path) == first
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert "POSTGRES_PASSWORD" not in path.name


def test_grub_patch_escapes_the_datasource_semicolon():
    original = "linux /casper/vmlinuz quiet ---\nlinux /casper/hwe-vmlinuz quiet ---\ngrub_platform\nif [ \"$grub_platform\" = \"efi\" ]; then\n"
    patched = patch_grub(original)
    assert patched.count(AUTOINSTALL_ARG) == 2
    assert "nocloud\\;" in patched
    assert "\ngrub_platform\n" not in f"\n{patched}"
    assert '$grub_platform' in patched
    assert patch_grub(patched) == patched


def test_first_boot_retries_then_records_completion(tmp_path):
    root = tmp_path / "opt"
    state = tmp_path / "state"
    logs = tmp_path / "log"
    bindir = tmp_path / "bin"
    bindir.mkdir()
    marker = bindir / "called"
    (bindir / "ansible-playbook").write_text(
        textwrap.dedent(
            f"""\
            #!/bin/bash
            echo called >> "{marker}"
            if [[ ! -f "{marker}.fail" ]]; then
              touch "{marker}.fail"
              exit 1
            fi
            exit 0
            """
        ),
        encoding="utf-8",
    )
    (bindir / "ansible-playbook").chmod(0o755)
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{bindir}:{env['PATH']}",
            "MAAS_DEPLOY_ROOT": str(root),
            "MAAS_STATE_DIR": str(state),
            "MAAS_LOG_DIR": str(logs),
        }
    )
    (root / "ansible").mkdir(parents=True)
    script = ROOT / "scripts" / "first-boot.sh"
    first = subprocess.run(["bash", str(script)], env=env, check=False, text=True)
    assert first.returncode != 0
    assert not (state / "complete").exists()
    second = subprocess.run(["bash", str(script)], env=env, check=False, text=True)
    assert second.returncode == 0
    assert (state / "complete").exists()
    third = subprocess.run(["bash", str(script)], env=env, check=False, text=True)
    assert third.returncode == 0
    assert marker.read_text(encoding="utf-8").count("called") == 2


def test_shell_scripts_parse():
    for script in (ROOT / "scripts").glob("*.sh"):
        subprocess.run(["bash", "-n", str(script)], check=True)
