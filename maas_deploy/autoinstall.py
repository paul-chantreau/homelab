"""Render the Autoinstall user-data consumed by the customized ISO."""

from __future__ import annotations

import secrets
import subprocess
from pathlib import Path
from typing import Any

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined

from maas_deploy.netplan import render_netplan

REFUSAL_SERIAL = "REFUSING-INCOMPLETE-CONFIG-NO-DISK-MATCH"


def _locked_password_hash() -> str:
    """Create an unrecoverable password hash.

    The random input is discarded. The hash cannot be used as a known password,
    and a late command also locks the account.
    """
    entropy = secrets.token_urlsafe(48).encode()
    completed = subprocess.run(
        ["openssl", "passwd", "-6", "-stdin"],
        input=entropy,
        check=True,
        capture_output=True,
    )
    return completed.stdout.decode().strip()


def disk_match(deployment: dict[str, Any], install_ready: bool) -> dict[str, str]:
    if not install_ready:
        return {"serial": REFUSAL_SERIAL}
    disk = deployment["disk"]
    match: dict[str, str] = {}
    for key in ("serial", "wwn"):
        if disk.get(key):
            match[key] = str(disk[key])
    if disk.get("by_id"):
        match["path"] = str(disk["by_id"])
    if disk.get("model") and match:
        match["model"] = str(disk["model"])
    if not match:
        return {"serial": REFUSAL_SERIAL}
    return match


def render_autoinstall(
    network: dict[str, Any],
    deployment: dict[str, Any],
    install_ready: bool,
) -> str:
    """Return cloud-config autoinstall user-data.

    An ISO built from an incomplete configuration still boots, then stops before
    partitioning. Its storage match is an impossible serial so a skipped precheck
    cannot fall through to the largest disk.
    """
    template_dir = Path(__file__).resolve().parents[1] / "autoinstall"
    environment = Environment(
        loader=FileSystemLoader(template_dir),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
        autoescape=False,
    )
    network_yaml = ""
    if install_ready:
        parsed = yaml.safe_load(render_netplan(network))["network"]
        network_yaml = "\n".join(
            "    " + line if line else ""
            for line in yaml.safe_dump(parsed, sort_keys=False).splitlines()
        )
    rendered = environment.get_template("autoinstall.yaml.j2").render(
        locale=deployment["locale"],
        keyboard_layout=deployment["keyboard_layout"],
        keyboard_variant=deployment.get("keyboard_variant", ""),
        timezone=deployment["timezone"],
        hostname=deployment["hostname"],
        admin_user=deployment["admin_user"],
        password_hash=_locked_password_hash(),
        ssh_keys=list(deployment.get("ssh_authorized_keys") or []),
        disk_match=disk_match(deployment, install_ready),
        install_ready=install_ready,
        network_yaml=network_yaml,
    )
    yaml.safe_load(rendered)
    return rendered


def render_meta_data(deployment: dict[str, Any]) -> str:
    return f"instance-id: {deployment['hostname']}\nlocal-hostname: {deployment['hostname']}\n"
