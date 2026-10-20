"""ISO payload staging and boot-catalog preservation."""

import hashlib
import subprocess
from pathlib import Path

from maas_deploy.iso import inject, install_ready, stage_payload

ROOT = Path(__file__).resolve().parents[1]


def test_staged_payload_refuses_install_for_the_committed_config(committed, tmp_path):
    destination = tmp_path / "payload"
    stage_payload(ROOT, destination, committed, install_ready(committed))
    user_data = (destination / "autoinstall" / "user-data").read_text(encoding="utf-8")
    assert "REFUSING-INCOMPLETE-CONFIG-NO-DISK-MATCH" in user_data
    assert not (destination / "generated" / "netplan.yaml").exists()
    assert (destination / "scripts" / "activate-dhcp.sh").stat().st_mode & 0o111


def test_xorriso_replay_keeps_a_grub_iso_bootable(tmp_path):
    source_dir = tmp_path / "tree"
    grub_dir = source_dir / "boot" / "grub"
    grub_dir.mkdir(parents=True)
    (grub_dir / "grub.cfg").write_text(
        "\n".join(
            [
                "serial --unit=0 --speed=115200",
                "terminal_input serial",
                "terminal_output serial",
                "echo MAAS-ISO-BOOT-OK",
                "halt",
                "",
            ]
        ),
        encoding="utf-8",
    )
    source_iso = tmp_path / "source.iso"
    grub = grub_dir / "grub.cfg"
    (source_dir / "md5sum.txt").write_text(
        f"{hashlib.md5(grub.read_bytes(), usedforsecurity=False).hexdigest()}  ./boot/grub/grub.cfg\n",
        encoding="utf-8",
    )
    subprocess.run(["grub-mkrescue", "-o", str(source_iso), str(source_dir)], check=True, capture_output=True)
    payload = tmp_path / "payload"
    payload.mkdir()
    (payload / "marker").write_text("payload\n", encoding="utf-8")
    output = tmp_path / "custom.iso"
    inject(source_iso, output, payload, [])
    completed = subprocess.run(
        [
            "timeout",
            "20",
            "qemu-system-x86_64",
            "-m",
            "256",
            "-display",
            "none",
            "-serial",
            "stdio",
            "-boot",
            "d",
            "-cdrom",
            str(output),
            "-no-reboot",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert "MAAS-ISO-BOOT-OK" in completed.stdout + completed.stderr


def test_injected_iso_checksums_cover_patched_grub_and_payload(tmp_path):
    source_dir = tmp_path / "tree"
    grub_dir = source_dir / "boot" / "grub"
    grub_dir.mkdir(parents=True)
    originals = {"boot/grub/grub.cfg": "original grub\n", "boot/grub/loopback.cfg": "original loopback\n",
                 "unchanged": "unchanged Ubuntu file\n"}
    lines = []
    for name, content in originals.items():
        (source_dir / name).write_text(content, encoding="utf-8")
        lines.append(f"{hashlib.md5(content.encode(), usedforsecurity=False).hexdigest()}  ./{name}")
    (source_dir / "md5sum.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    source_iso = tmp_path / "source.iso"
    subprocess.run(["xorriso", "-as", "mkisofs", "-o", str(source_iso), str(source_dir)],
                   check=True, capture_output=True)
    patched = []
    for name in ("grub.cfg", "loopback.cfg"):
        local = tmp_path / name
        local.write_text("patched autoinstall boot command\n", encoding="utf-8")
        patched.append((local, f"/boot/grub/{name}"))
    payload = tmp_path / "payload"
    payload.mkdir()
    (payload / "user-data").write_text("injected configuration\n", encoding="utf-8")
    output = tmp_path / "custom.iso"
    inject(source_iso, output, payload, patched)
    extracted = tmp_path / "extracted"
    subprocess.run(["xorriso", "-osirrox", "on", "-indev", str(output), "-extract", "/", str(extracted)],
                   check=True, capture_output=True)
    verification = subprocess.run(["md5sum", "--check", "md5sum.txt"], cwd=extracted,
                                  capture_output=True, text=True)
    assert verification.returncode == 0, verification.stdout + verification.stderr
    assert len((extracted / "md5sum.txt").read_text().splitlines()) == 4
    assert "./maas-deploy/user-data: OK" in verification.stdout
    (extracted / "maas-deploy" / "user-data").write_text("corrupted payload\n", encoding="utf-8")
    assert subprocess.run(["md5sum", "--check", "md5sum.txt"], cwd=extracted,
                          capture_output=True).returncode != 0
