"""Build a UEFI-bootable Ubuntu autoinstall ISO without extracting it."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from maas_deploy.autoinstall import render_autoinstall, render_meta_data
from maas_deploy.config import INSTALL_MODE, SCHEMA_MODE, ConfigError, LoadedConfig, validate
from maas_deploy.grub import patch_grub
from maas_deploy.netplan import render_netplan

PAYLOAD_DIRS = ("maas_deploy", "ansible", "scripts", "config", "systemd", "autoinstall")
IGNORE_NAMES = {"__pycache__", ".pytest_cache", ".git"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def published_digest(sha256sums: str, iso_name: str) -> str | None:
    for line in sha256sums.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        name = parts[-1].lstrip("*")
        if name == iso_name:
            return parts[0].lower()
    return None


def install_ready(config: LoadedConfig) -> bool:
    try:
        validate(config, INSTALL_MODE)
    except ConfigError:
        return False
    return True


def stage_payload(source_root: Path, destination: Path, config: LoadedConfig, ready: bool) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    for name in PAYLOAD_DIRS:
        source = source_root / name
        if source.exists():
            shutil.copytree(
                source,
                destination / name,
                ignore=shutil.ignore_patterns(*IGNORE_NAMES, "*.pyc"),
            )
    autoinstall = destination / "autoinstall"
    autoinstall.mkdir(parents=True, exist_ok=True)
    (autoinstall / "user-data").write_text(
        render_autoinstall(config.network, config.deployment, ready, template_dir=source_root / "autoinstall"),
        encoding="utf-8",
    )
    (autoinstall / "meta-data").write_text(render_meta_data(config.deployment), encoding="utf-8")
    generated = destination / "generated"
    generated.mkdir()
    if ready:
        (generated / "netplan.yaml").write_text(render_netplan(config.network), encoding="utf-8")
    (generated / "install-ready").write_text("true\n" if ready else "false\n", encoding="utf-8")
    for script in (destination / "scripts").glob("*"):
        if script.is_file():
            script.chmod(0o755)


def extract_grub(iso_path: Path, work: Path) -> list[tuple[Path, str]]:
    """Return local patched files and their ISO destinations."""
    targets = ["/boot/grub/grub.cfg", "/boot/grub/loopback.cfg"]
    extracted: list[tuple[Path, str]] = []
    for iso_path_name in targets:
        local = work / Path(iso_path_name).name
        result = subprocess.run(
            ["xorriso", "-osirrox", "on", "-indev", str(iso_path), "-extract", iso_path_name, str(local)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0 or not local.exists():
            continue
        local.chmod(0o644)
        local.write_text(patch_grub(local.read_text(encoding="utf-8", errors="replace")), encoding="utf-8")
        extracted.append((local, iso_path_name))
    if not extracted:
        raise RuntimeError("the source ISO does not contain /boot/grub/grub.cfg")
    return extracted


def inject(source_iso: Path, output_iso: Path, payload: Path, grub_files: list[tuple[Path, str]]) -> None:
    if output_iso.exists():
        output_iso.unlink()
    command = ["xorriso", "-indev", str(source_iso), "-outdev", str(output_iso), "-map", str(payload), "/maas-deploy"]
    for local, iso_path_name in grub_files:
        command.extend(["-map", str(local), iso_path_name])
    # no_emul_toc keeps the appended EFI partition from overlapping the ISO
    # after files are added. Without it, xorriso can reject an otherwise bootable image.
    command.extend(["-compliance", "no_emul_toc", "-boot_image", "any", "replay"])
    subprocess.run(command, check=True)


def build(
    source_root: Path,
    source_iso: Path,
    output_iso: Path,
    config: LoadedConfig,
    sha256sums_text: str,
) -> dict[str, Any]:
    validate(config, SCHEMA_MODE)
    ubuntu = config.deployment["ubuntu"]
    published = published_digest(sha256sums_text, ubuntu["iso_name"])
    if published is None:
        raise RuntimeError(f"SHA256SUMS does not list {ubuntu['iso_name']}")
    if published != str(ubuntu["sha256"]).lower():
        raise RuntimeError("deployment.ubuntu.sha256 does not match the published SHA256SUMS entry")
    observed = sha256_file(source_iso)
    if observed != published:
        raise RuntimeError("the downloaded Ubuntu ISO checksum does not match SHA256SUMS")
    ready = install_ready(config)
    output_iso.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="maas-iso-") as temporary:
        work = Path(temporary)
        payload = work / "payload"
        stage_payload(source_root, payload, config, ready)
        grub_files = extract_grub(source_iso, work)
        inject(source_iso, output_iso, payload, grub_files)
    manifest = {
        "artifact": output_iso.name,
        "sha256": sha256_file(output_iso),
        "source_iso": ubuntu["iso_name"],
        "source_sha256": observed,
        "source_url": ubuntu["base_url"] + ubuntu["iso_name"],
        "install_ready": ready,
        "maas_channel": config.deployment["maas"]["channel"],
        "postgresql_major": config.deployment["postgresql"]["major"],
        "config_hash": config.canonical_hash,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "builder": "maas_deploy.iso",
    }
    manifest_path = output_iso.parent / "build-manifest.json"
    sums_path = output_iso.parent / "SHA256SUMS"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    sums_path.write_text(f"{manifest['sha256']}  {output_iso.name}\n", encoding="utf-8")
    os.chmod(output_iso, 0o644)
    return manifest
