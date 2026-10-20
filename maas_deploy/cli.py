"""Operator entry points used by scripts and Ansible."""

from __future__ import annotations

import json
import os
import sys
import subprocess
from pathlib import Path

from maas_deploy.access import display_access
from maas_deploy.config import INSTALL_MODE, ConfigError, load_config, validate
from maas_deploy.iso import build
from maas_deploy.netplan import render_netplan
from maas_deploy.maas_network import PlanError
from maas_deploy.runtime import SetupError, apply_dhcp, apply_images, apply_maas, apply_network, apply_postgres


def _root() -> Path:
    return Path(os.environ.get("MAAS_DEPLOY_ROOT", "/opt/maas-deploy"))


def _state() -> Path:
    return Path(os.environ.get("MAAS_STATE_DIR", "/var/lib/maas-provision"))


def _config():
    root = _root()
    return load_config(root / "config" / "network.yaml", root / "config" / "deployment.yaml")


def _die(message: str, code: int = 1) -> int:
    print(message, file=sys.stderr)
    return code


def command_render_netplan() -> int:
    config = _config()
    validate(config, INSTALL_MODE)
    destination = Path("/etc/netplan/99-maas-provision.yaml")
    destination.write_text(render_netplan(config.network), encoding="utf-8")
    os.chmod(destination, 0o600)
    print(destination)
    return 0


def command_postgres() -> int:
    config = _config()
    validate(config, INSTALL_MODE)
    print(apply_postgres(config, Path("/etc/maas-provision/secrets.env")))
    return 0


def command_maas() -> int:
    config = _config()
    validate(config, INSTALL_MODE)
    print(apply_maas(config, Path("/etc/maas-provision/secrets.env")))
    return 0


def command_network() -> int:
    config = _config()
    validate(config, INSTALL_MODE)
    print(apply_network(config, Path("/etc/maas-provision/secrets.env"), _state()))
    return 0


def command_backup() -> int:
    from maas_deploy.backup import backup_controller
    config = _config()
    validate(config, INSTALL_MODE)
    print(backup_controller(config, _root()))
    return 0


def command_restore() -> int:
    from maas_deploy.backup import restore_database
    from maas_deploy.runtime import _wait_for_maas
    config = _config()
    validate(config, INSTALL_MODE)
    previous = restore_database(config.deployment["postgresql"]["database"],
                                config.deployment["postgresql"]["user"], Path(sys.argv[2]),
                                readiness=_wait_for_maas)
    print(f"Restored MAAS; previous database retained as {previous}")
    return 0


def command_healthcheck() -> int:
    from maas_deploy.healthcheck import collect, evaluate as health_evaluate
    config = _config()
    validate(config, INSTALL_MODE)
    report = health_evaluate(collect(config, Path("/etc/maas-provision/secrets.env"), _state()))
    print(json.dumps(report, indent=2))
    return 0 if report["outcome"] == "pass" else 1


def command_images() -> int:
    config = _config()
    validate(config, INSTALL_MODE)
    marker = _state() / "images-synced"
    print(apply_images(config, Path("/etc/maas-provision/secrets.env"), marker))
    return 0


def command_console_access() -> int:
    config = _config()
    validate(config, INSTALL_MODE)
    display_access(config, Path("/etc/maas-provision/secrets.env"))
    return 0


def _dhcp_change(enabled: bool, phrase: str, emergency: bool = False) -> int:
    config = _config()
    validate(config, INSTALL_MODE)
    vlan_id = int(config.network["provisioning"]["vlan_id"])
    if enabled:
        expected = f"ACTIVATE DHCP ON VLAN {vlan_id}"
    elif emergency:
        expected = f"EMERGENCY DEACTIVATE DHCP ON VLAN {vlan_id}"
    else:
        expected = f"DEACTIVATE DHCP ON VLAN {vlan_id}"
    if phrase != expected:
        return _die("Confirmation did not match. DHCP was not changed.")
    subnet = config.network["provisioning"]["subnet"]
    print(f"Provisioning subnet: {subnet}")
    print(f"Provisioning VLAN: {vlan_id}")
    print("Management VLAN DHCP is not changed by this command.")
    apply_dhcp(config, Path("/etc/maas-provision/secrets.env"), _state(), enabled=enabled)
    print("DHCP is active." if enabled else "DHCP is stopped.")
    return 0


def command_dhcp_enable() -> int:
    return _dhcp_change(True, os.environ.get("MAAS_DHCP_CONFIRM", ""))


def command_dhcp_disable() -> int:
    emergency = os.environ.get("MAAS_DHCP_EMERGENCY") == "1"
    return _dhcp_change(False, os.environ.get("MAAS_DHCP_CONFIRM", ""), emergency)


def command_build_iso() -> int:
    root = _root()
    config = load_config(root / "config" / "network.yaml", root / "config" / "deployment.yaml")
    source = Path(os.environ["UBUNTU_ISO"])
    output = Path(os.environ["OUTPUT_ISO"])
    sums = Path(os.environ["SHA256SUMS"]).read_text(encoding="utf-8")
    manifest = build(root, source, output, config, sums)
    print(json.dumps({"install_ready": manifest["install_ready"], "sha256": manifest["sha256"]}))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        return _die("missing command")
    commands = {
        "render-netplan": command_render_netplan,
        "postgres": command_postgres,
        "maas": command_maas,
        "network": command_network,
        "images": command_images,
        "console-access": command_console_access,
        "healthcheck": command_healthcheck,
        "backup": command_backup,
        "restore": command_restore,
        "dhcp-enable": command_dhcp_enable,
        "dhcp-disable": command_dhcp_disable,
        "build-iso": command_build_iso,
    }
    try:
        handler = commands.get(args[0])
        if handler is None:
            return _die(f"unknown command {args[0]}")
        return handler()
    except (ConfigError, PlanError, SetupError, OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        return _die(str(exc))


if __name__ == "__main__":
    sys.exit(main())
