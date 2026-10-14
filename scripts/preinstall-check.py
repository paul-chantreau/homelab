#!/usr/bin/env python3
"""Stop autoinstall before any disk is erased when the configuration is unsafe."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if (ROOT / "maas_deploy").is_dir():
    sys.path.insert(0, str(ROOT))
else:
    sys.path.insert(0, "/cdrom/maas-deploy")

from maas_deploy.config import INSTALL_MODE, ConfigError, load_config, validate  # noqa: E402
from maas_deploy.diskmatch import describe, matching_disks  # noqa: E402


def _disks() -> list[dict]:
    raw = subprocess.check_output(
        ["lsblk", "-J", "-b", "-o", "NAME,KNAME,TYPE,SIZE,MODEL,SERIAL,WWN"],
        text=True,
    )
    devices: list[dict] = []

    def walk(nodes: list[dict]) -> None:
        for node in nodes:
            devices.append(
                {
                    "name": node.get("kname") or node.get("name"),
                    "type": node.get("type"),
                    "size": node.get("size"),
                    "model": (node.get("model") or "").strip(),
                    "serial": (node.get("serial") or "").strip(),
                    "wwn": (node.get("wwn") or "").strip(),
                    "by_id_paths": [],
                }
            )
            walk(node.get("children") or [])

    walk(json.loads(raw).get("blockdevices") or [])
    by_id = Path("/dev/disk/by-id")
    if by_id.is_dir():
        for entry in by_id.iterdir():
            target = Path(os.path.realpath(entry)).name
            for device in devices:
                if device["name"] == target:
                    device["by_id_paths"].append(str(entry))
    return [device for device in devices if device["type"] == "disk"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--network", type=Path, required=True)
    parser.add_argument("--deployment", type=Path, required=True)
    args = parser.parse_args()
    try:
        config = load_config(args.network, args.deployment)
        validate(config, INSTALL_MODE)
    except ConfigError as exc:
        print("Refusing to install. The configuration is incomplete or unsafe:", file=sys.stderr)
        print(exc, file=sys.stderr)
        print("No disk was erased.", file=sys.stderr)
        return 1
    matches = matching_disks(_disks(), config.deployment["disk"])
    if len(matches) != 1:
        print(
            f"Refusing to install. Disk selection matched {len(matches)} disks. "
            "No disk was erased.",
            file=sys.stderr,
        )
        return 1
    print(f"The installer will erase this disk: {describe(matches[0])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
