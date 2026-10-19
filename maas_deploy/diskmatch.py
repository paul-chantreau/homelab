"""Match the installation disk by stable identifiers.

The largest disk is never a candidate. Kernel names such as /dev/sda are never
accepted as selectors.
"""

from __future__ import annotations

from typing import Any
from pathlib import Path
import subprocess

import yaml


def update_installer_serial(path: Path, device: dict[str, Any], spec: dict[str, Any]) -> None:
    """Translate a verified lsblk serial to Subiquity's udev ID_SERIAL.

    Called only after the configured identity matches exactly one physical disk.
    The canonical deployment configuration retains the hardware's short serial.
    """
    if not spec.get("serial"):
        return
    name = str(device["name"])
    result = subprocess.run(
        ["udevadm", "info", "--query=property", f"--name=/dev/{name}"],
        check=True, capture_output=True, text=True,
    )
    properties = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    serial = properties.get("ID_SERIAL", "").strip()
    short = properties.get("ID_SERIAL_SHORT", "").strip()
    if not serial or (short and _norm(short) != _norm(spec["serial"])):
        raise ValueError("Cannot resolve the verified disk's installer serial; refusing installation")
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        # Subiquity extracts cloud-config's autoinstall section into a flat
        # /autoinstall.yaml before running early-commands. Preserve either form.
        if not isinstance(document, dict):
            raise ValueError("Installer configuration must be a mapping")
        config = document.get("autoinstall", document)
        match = config["storage"]["layout"]["match"]
        if not isinstance(match, dict):
            raise ValueError("Installer disk selector must be a mapping")
    except (KeyError, TypeError, yaml.YAMLError) as error:
        raise ValueError("Invalid installer storage configuration; refusing installation") from error
    if match.get("serial") not in {str(spec["serial"]), serial}:
        raise ValueError("Installer disk selector differs from verified configuration; refusing installation")
    match["serial"] = serial
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")


def matching_disks(devices: list[dict[str, Any]], spec: dict[str, Any]) -> list[dict[str, Any]]:
    serial = _norm(spec.get("serial"))
    wwn = _norm_wwn(spec.get("wwn"))
    by_id = spec.get("by_id")
    model = _norm(spec.get("model"))
    if not serial and not wwn and not by_id:
        return []
    found = []
    for device in devices:
        if device.get("type") not in {None, "disk"}:
            continue
        if serial and _norm(device.get("serial")) != serial:
            continue
        if wwn and _norm_wwn(device.get("wwn")) != wwn:
            continue
        if by_id and by_id not in set(device.get("by_id_paths") or []):
            continue
        if model and _norm(device.get("model")) != model:
            continue
        found.append(device)
    return found


def describe(device: dict[str, Any]) -> str:
    name = device.get("name") or device.get("kname") or "unknown"
    serial = device.get("serial") or "no-serial"
    wwn = device.get("wwn") or "no-wwn"
    model = device.get("model") or "no-model"
    size = device.get("size") or "unknown-size"
    return f"{name} model={model} serial={serial} wwn={wwn} size={size}"


def _norm(value: Any) -> str:
    if value in (None, ""):
        return ""
    return str(value).strip().lower()


def _norm_wwn(value: Any) -> str:
    text = _norm(value)
    if text.startswith("0x"):
        text = text[2:]
    return text
