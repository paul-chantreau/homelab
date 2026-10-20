"""Operator entry points used by scripts and Ansible."""

from __future__ import annotations

import json
import os
import sys
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from maas_deploy.acceptance import evaluate
from maas_deploy.config import HARDWARE_REVISION, INSTALL_MODE, ConfigError, load_config, validate
from maas_deploy.dhcp_gate import activation_phrase, assess_activation, deactivation_phrase, emergency_phrase
from maas_deploy.iso import build
from maas_deploy.netplan import render_netplan
from maas_deploy.maas_network import controller_vlan, plan_disable, plan_enable, render_maas_command
from maas_deploy.facts import default_route, interfaces_from_ip, parse_maas_status
from maas_deploy.runtime import SetupError, _run, apply_images, apply_maas, apply_network, apply_postgres, inventory
from maas_deploy.secrets import load


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
    print(apply_network(config, Path("/etc/maas-provision/secrets.env")))
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


def command_write_validation() -> int:
    config = _config()
    validate(config, INSTALL_MODE)
    facts = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    report = evaluate(config.network, facts, config.canonical_hash)
    report["generated_at"] = datetime.now(timezone.utc).isoformat()
    destination = _state() / "vlan-validation.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"outcome={report['outcome']}")
    for name, check in report["checks"].items():
        print(f"{check['status']:13} {name}: {check['detail']}")
    if report["outcome"] == "PASS":
        return 0
    if report["outcome"] == "INCONCLUSIVE":
        return 2
    return 1


def _device_from_route(text: str) -> str | None:
    parts = text.split()
    if "dev" not in parts:
        return None
    return parts[parts.index("dev") + 1]


def command_assemble_facts() -> int:
    """Combine captured command output into the acceptance fact document."""
    work = Path(sys.argv[2])
    test_address = (work / "test.address").read_text(encoding="utf-8").strip()
    facts: dict = {
        "interfaces": interfaces_from_ip(
            (work / "address.json").read_text(encoding="utf-8"),
            (work / "link.json").read_text(encoding="utf-8"),
        ),
        "default_route": default_route((work / "route.json").read_text(encoding="utf-8")),
        "management_gateway": {
            "reachable": (work / "gateway.ping").read_text(encoding="utf-8").strip() == "yes",
            "device": _device_from_route((work / "gateway.route").read_text(encoding="utf-8")),
        },
        "test_host": {
            "address": None if test_address in {"", "-"} else test_address,
            "ping": (work / "test.ping").read_text(encoding="utf-8").strip() == "yes",
            "arping": (work / "test.arping").read_text(encoding="utf-8").strip() == "yes",
            "probe": json.loads((work / "test.reverse").read_text(encoding="utf-8")) if (work / "test.reverse").read_text().strip() else {},
        },
        "dhcp_capture": json.loads((work / "dhcp.json").read_text(encoding="utf-8")) if (work / "dhcp.json").exists() else {"ran": False},
        "maas_rack": parse_maas_status((work / "maas-status.txt").read_text(encoding="utf-8")),
    }
    isolation = work / "isolation.json"
    if isolation.exists():
        facts["isolation_report"] = json.loads(isolation.read_text(encoding="utf-8"))
    confirmation = _state() / "gs724t-confirmation.json"
    if confirmation.exists():
        facts["switch_confirmation"] = json.loads(confirmation.read_text(encoding="utf-8"))
    (work / "facts.json").write_text(json.dumps(facts, indent=2) + "\n", encoding="utf-8")
    return 0


def _dhcp_change(enabled: bool, phrase: str, emergency: bool = False) -> int:
    config = _config()
    validate(config, INSTALL_MODE)
    vlan_id = int(config.network["provisioning"]["vlan_id"])
    if enabled:
        expected = activation_phrase(vlan_id)
    elif emergency:
        expected = emergency_phrase(vlan_id)
    else:
        expected = deactivation_phrase(vlan_id)
    if phrase != expected:
        return _die("Confirmation did not match. DHCP was not changed.")
    report_path = _state() / "vlan-validation.json"
    if enabled:
        if not report_path.exists():
            return _die("VLAN validation has not been recorded.")
        report = json.loads(report_path.read_text(encoding="utf-8"))
        allowed, reason = assess_activation(report, config.canonical_hash)
        if not allowed:
            return _die(f"Refusing to activate DHCP: {reason}")
    profile = config.deployment["maas"]["admin_username"]
    secrets = load(Path("/etc/maas-provision/secrets.env"))
    _run(["maas", "login", profile, "http://127.0.0.1:5240/MAAS/", secrets["MAAS_API_KEY"]])
    current = inventory(profile)
    actions = plan_enable(config.network, current) if enabled else plan_disable(config.network, current)
    subnet = config.network["provisioning"]["subnet"]
    print(f"Provisioning subnet: {subnet}")
    print(f"Provisioning VLAN: {vlan_id}")
    print("Management VLAN DHCP is not changed by this command.")
    for action in actions:
        if int(action.get("vid", vlan_id)) != vlan_id:
            return _die("Refusing a DHCP change that does not target the provisioning VLAN.")
        _run(render_maas_command(profile, action))
    confirmed = bool(controller_vlan(config.network, inventory(profile)).get("dhcp_on"))
    if confirmed is not enabled:
        return _die(f"MAAS reports dhcp_on={confirmed} after the change.")
    marker = _state() / "dhcp-active"
    if enabled:
        marker.write_text(f"vlan {vlan_id}\n", encoding="utf-8")
    elif marker.exists():
        marker.unlink()
    print("DHCP is active." if enabled else "DHCP is stopped.")
    return 0


def command_dhcp_enable() -> int:
    return _dhcp_change(True, os.environ.get("MAAS_DHCP_CONFIRM", ""))


def command_dhcp_disable() -> int:
    emergency = os.environ.get("MAAS_DHCP_EMERGENCY") == "1"
    return _dhcp_change(False, os.environ.get("MAAS_DHCP_CONFIRM", ""), emergency)


def command_confirm_switch() -> int:
    config = _config()
    validate(config, INSTALL_MODE)
    revision = HARDWARE_REVISION
    vlan_id = int(config.network["provisioning"]["vlan_id"])
    expected = f"CONFIRM GS724T {revision} VLAN {vlan_id}"
    if os.environ.get("MAAS_SWITCH_CONFIRM", "") != expected:
        return _die("Switch confirmation did not match. No record was written.")
    document = {
        "phrase_ok": True,
        "config_hash": config.canonical_hash,
        "hardware_revision": revision,
        "confirmed_at": datetime.now(timezone.utc).isoformat(),
        "user": os.environ.get("SUDO_USER") or os.environ.get("USER") or "root",
    }
    path = _state() / "gs724t-confirmation.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    os.chmod(path, 0o600)
    print(f"Recorded GS724T confirmation for VLAN {vlan_id}.")
    return 0


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
        "healthcheck": command_healthcheck,
        "backup": command_backup,
        "restore": command_restore,
        "assemble-facts": command_assemble_facts,
        "write-validation": command_write_validation,
        "dhcp-enable": command_dhcp_enable,
        "dhcp-disable": command_dhcp_disable,
        "confirm-switch": command_confirm_switch,
        "build-iso": command_build_iso,
    }
    try:
        handler = commands.get(args[0])
        if handler is None:
            return _die(f"unknown command {args[0]}")
        return handler()
    except (ConfigError, SetupError, OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        return _die(str(exc))


if __name__ == "__main__":
    sys.exit(main())
