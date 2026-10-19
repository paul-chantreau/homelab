"""Load and validate the canonical network and deployment configuration.

Unknown physical values may be null while the repository is prepared. Values
that are present are always checked. Installation mode additionally requires
every field that would otherwise make disk erasure or DHCP activation unsafe.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

SCHEMA_MODE = "schema"
INSTALL_MODE = "install"

# This deployment supports only the GS724Tv4. Ports 1–26 cover 24 copper ports and 2 SFP ports.
HARDWARE_REVISION = "GS724Tv4"
PORT_MAX = 26
# The v4 manual predefines VLAN 1 (default), VLAN 2 (voice), and VLAN 3 (auto-video).
RESERVED_PROVISIONING_VLANS = {1, 2, 3}
VLAN_MAX = 4093
MAAS_CHANNEL = "3.7/stable"
MAAS_MODE = "region+rack"
POSTGRES_MAJOR = 16
ROUTING_DECISION = "isolated"

_MAC = re.compile(r"^([0-9a-f]{2}:){5}[0-9a-f]{2}$")
_HOSTNAME = re.compile(r"^(?=.{1,63}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_TIMEZONE = re.compile(r"^[A-Za-z0-9_+-]+(?:/[A-Za-z0-9_+-]+)*$")
_USER = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SSH_PUBLIC = re.compile(
    r"^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp256|ecdsa-sha2-nistp384|ecdsa-sha2-nistp521) [A-Za-z0-9+/=]+(?: .*)?$"
)
_PRIVATE_KEY = re.compile(r"BEGIN [A-Z ]*PRIVATE KEY")
_BY_ID = re.compile(r"^/dev/disk/by-id/[A-Za-z0-9._+-]+$")

NETWORK_KEYS = {"mode", "routing", "switch", "management", "provisioning"}
ROUTING_KEYS = {"decision", "ubuntu_nat"}
SWITCH_KEYS = {
    "vendor",
    "model",
    "hardware_revision",
    "firmware_version",
    "router_port",
    "maas_port",
    "provisioning_ports",
}
MANAGEMENT_KEYS = {"interface_mac", "address", "gateway", "dns", "vlan_id"}
PROVISIONING_KEYS = {
    "vlan_id",
    "subnet",
    "controller_address",
    "dhcp",
    "gateway",
    "dns",
    "acceptance",
}
DHCP_KEYS = {"activate_automatically", "range_start", "range_end"}
ACCEPTANCE_KEYS = {"test_host"}
DEPLOYMENT_KEYS = {
    "hostname",
    "timezone",
    "locale",
    "keyboard_layout",
    "keyboard_variant",
    "admin_user",
    "ssh_authorized_keys",
    "disk",
    "ubuntu",
    "maas",
    "postgresql",
    "backup",
}
DISK_KEYS = {"serial", "wwn", "by_id", "model", "confirm_erase"}
UBUNTU_KEYS = {"release", "codename", "iso_name", "sha256", "base_url"}
MAAS_KEYS = {"channel", "mode", "admin_username", "admin_email", "image_release"}
POSTGRES_KEYS = {"major", "database", "user"}
BACKUP_KEYS = {"directory", "offhost_target", "retain"}


class ConfigError(Exception):
    """One or more configuration checks failed."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("\n".join(errors))


@dataclass(frozen=True)
class LoadedConfig:
    network: dict[str, Any]
    deployment: dict[str, Any]
    network_path: Path | None = None
    deployment_path: Path | None = None

    @property
    def canonical_hash(self) -> str:
        payload = {"deployment": self.deployment, "network": self.network}
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()


def load_yaml(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def load_config(network_path: Path, deployment_path: Path) -> LoadedConfig:
    network_doc = load_yaml(network_path)
    deployment_doc = load_yaml(deployment_path)
    if not isinstance(network_doc, dict) or "network" not in network_doc:
        raise ConfigError(["network.yaml must contain a top-level 'network' mapping"])
    if not isinstance(deployment_doc, dict) or "deployment" not in deployment_doc:
        raise ConfigError(["deployment.yaml must contain a top-level 'deployment' mapping"])
    return LoadedConfig(
        network=network_doc["network"],
        deployment=deployment_doc["deployment"],
        network_path=network_path,
        deployment_path=deployment_path,
    )


def validate(config: LoadedConfig, mode: str = SCHEMA_MODE) -> None:
    if mode not in {SCHEMA_MODE, INSTALL_MODE}:
        raise ConfigError([f"unknown validation mode: {mode}"])
    errors: list[str] = []
    _reject_private_material(config.network, "network", errors)
    _reject_private_material(config.deployment, "deployment", errors)
    _validate_network(config.network, mode, errors)
    _validate_deployment(config.deployment, mode, errors)
    if errors:
        raise ConfigError(errors)


def _reject_private_material(value: Any, location: str, errors: list[str]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key.lower() in {"password", "secret", "token", "private_key"} and item not in (None, ""):
                errors.append(f"{location}.{key} must not store a credential")
            _reject_private_material(item, f"{location}.{key}", errors)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_private_material(item, f"{location}[{index}]", errors)
    elif isinstance(value, str) and _PRIVATE_KEY.search(value):
        errors.append(f"{location} contains a private key")


def _unknown(mapping: Any, allowed: set[str], location: str, errors: list[str]) -> dict[str, Any]:
    if not isinstance(mapping, dict):
        errors.append(f"{location} must be a mapping")
        return {}
    unexpected = sorted(set(mapping) - allowed)
    if unexpected:
        errors.append(f"{location} has unknown fields: {', '.join(unexpected)}")
    return mapping


def _require(mapping: dict[str, Any], key: str, location: str, errors: list[str], mode: str) -> Any:
    if key not in mapping:
        errors.append(f"{location}.{key} is missing")
        return None
    value = mapping[key]
    if mode == INSTALL_MODE and value in (None, "", [], {}):
        errors.append(f"{location}.{key} is required before installation")
    return value


def _validate_network(network: Any, mode: str, errors: list[str]) -> None:
    network = _unknown(network, NETWORK_KEYS, "network", errors)
    if network.get("mode") != "vlan":
        errors.append(
            "network.mode must be 'vlan'. A shared LAN is not selected automatically "
            "and requires a separately approved DHCP migration plan"
        )
    routing = _unknown(network.get("routing"), ROUTING_KEYS, "network.routing", errors)
    if routing.get("decision") != ROUTING_DECISION:
        errors.append(
            "network.routing.decision must be 'isolated'. Router, switch Layer-3, "
            "and Ubuntu NAT paths are documented but not enabled by this deployment"
        )
    if routing.get("ubuntu_nat") is not False:
        errors.append("network.routing.ubuntu_nat must be false")

    switch = _unknown(network.get("switch"), SWITCH_KEYS, "network.switch", errors)
    if switch.get("vendor") != "netgear":
        errors.append("network.switch.vendor must be 'netgear'")
    if str(switch.get("model", "")).upper() != "GS724T":
        errors.append("network.switch.model must be 'GS724T'")
    revision = switch.get("hardware_revision")
    if revision != HARDWARE_REVISION:
        errors.append("network.switch.hardware_revision must be 'GS724Tv4'")
    firmware = switch.get("firmware_version")
    if mode == INSTALL_MODE and firmware in (None, ""):
        errors.append("network.switch.firmware_version is required before installation")
    elif firmware not in (None, "") and not isinstance(firmware, str):
        errors.append("network.switch.firmware_version must be a string")

    router = _port(switch.get("router_port"), "network.switch.router_port", PORT_MAX, mode, errors)
    maas = _port(switch.get("maas_port"), "network.switch.maas_port", PORT_MAX, mode, errors)
    provisioning_ports = switch.get("provisioning_ports")
    parsed_ports: list[int] = []
    if not isinstance(provisioning_ports, list):
        errors.append("network.switch.provisioning_ports must be a list")
    else:
        if mode == INSTALL_MODE and not provisioning_ports:
            errors.append("network.switch.provisioning_ports must name at least one port")
        for index, port in enumerate(provisioning_ports):
            parsed = _port(
                port,
                f"network.switch.provisioning_ports[{index}]",
                PORT_MAX,
                INSTALL_MODE,
                errors,
            )
            if parsed is not None:
                parsed_ports.append(parsed)
    assigned = [port for port in (router, maas, *parsed_ports) if port is not None]
    if len(assigned) != len(set(assigned)):
        errors.append("switch port roles must be unique across router, MAAS, and provisioning ports")

    management = _unknown(network.get("management"), MANAGEMENT_KEYS, "network.management", errors)
    mac = management.get("interface_mac")
    if mac not in (None, ""):
        _mac(mac, "network.management.interface_mac", errors)
    elif mode == INSTALL_MODE:
        errors.append("network.management.interface_mac is required before installation")
    management_net = _cidr(management.get("address"), "network.management.address", mode, errors, host=True)
    gateway = _ip(management.get("gateway"), "network.management.gateway", mode, errors)
    if management_net is not None and gateway is not None and gateway not in management_net:
        errors.append("network.management.gateway must be inside the management subnet")
    if (
        management_net is not None
        and gateway is not None
        and management.get("address") not in (None, "")
    ):
        host = ipaddress.IPv4Address(str(management["address"]).split("/", 1)[0])
        if gateway == host:
            errors.append("network.management.gateway must not be the controller address")
    dns = management.get("dns")
    _dns(dns, "network.management.dns", mode == INSTALL_MODE, errors)
    management_vlan = _vlan(management.get("vlan_id"), "network.management.vlan_id", errors, allow_one=True)

    provisioning = _unknown(
        network.get("provisioning"), PROVISIONING_KEYS, "network.provisioning", errors
    )
    provisioning_vlan = _vlan(
        provisioning.get("vlan_id"), "network.provisioning.vlan_id", errors, allow_one=False
    )
    if management_vlan is not None and provisioning_vlan is not None and management_vlan == provisioning_vlan:
        errors.append("management and provisioning VLAN IDs must be different")
    _reserved_vlan(provisioning_vlan, errors)
    provisioning_net = _cidr(
        provisioning.get("subnet"), "network.provisioning.subnet", mode, errors, host=False
    )
    controller = _ip(
        provisioning.get("controller_address"),
        "network.provisioning.controller_address",
        mode,
        errors,
    )
    if provisioning_net is not None and controller is not None and controller not in provisioning_net:
        errors.append("network.provisioning.controller_address must be inside the provisioning subnet")
    if provisioning_net is not None and controller is not None:
        if controller in {provisioning_net.network_address, provisioning_net.broadcast_address}:
            errors.append(
                "network.provisioning.controller_address must not be the subnet or broadcast address"
            )
    if management_net is not None and provisioning_net is not None and management_net.overlaps(provisioning_net):
        errors.append("management and provisioning subnets must not overlap")

    dhcp = _unknown(provisioning.get("dhcp"), DHCP_KEYS, "network.provisioning.dhcp", errors)
    if dhcp.get("activate_automatically") is not False:
        errors.append(
            "network.provisioning.dhcp.activate_automatically must be false. "
            "DHCP is enabled only by maas-network-activate after validation"
        )
    start = _ip(dhcp.get("range_start"), "network.provisioning.dhcp.range_start", mode, errors)
    end = _ip(dhcp.get("range_end"), "network.provisioning.dhcp.range_end", mode, errors)
    if start is not None and end is not None and start >= end:
        errors.append("DHCP range_start must be less than range_end")
    if provisioning_net is not None:
        for label, address in (("range_start", start), ("range_end", end)):
            if address is None:
                continue
            if address not in provisioning_net:
                errors.append(f"DHCP {label} must be inside the provisioning subnet")
            elif address in {provisioning_net.network_address, provisioning_net.broadcast_address}:
                errors.append(f"DHCP {label} must not be the subnet or broadcast address")
    if start is not None and end is not None and controller is not None and start <= controller <= end:
        errors.append("the controller address must be outside the dynamic DHCP pool")

    prov_gateway = _ip(
        provisioning.get("gateway"), "network.provisioning.gateway", SCHEMA_MODE, errors
    )
    if mode == INSTALL_MODE and "gateway" not in provisioning:
        errors.append("network.provisioning.gateway must be present (use null for an isolated VLAN)")
    if prov_gateway is not None and provisioning_net is not None and prov_gateway not in provisioning_net:
        errors.append("network.provisioning.gateway must be inside the provisioning subnet")
    if prov_gateway is not None and controller is not None and prov_gateway == controller:
        errors.append("network.provisioning.gateway must not be the MAAS controller address")
    if (
        prov_gateway is not None
        and start is not None
        and end is not None
        and start <= prov_gateway <= end
    ):
        errors.append("network.provisioning.gateway must be outside the dynamic DHCP pool")
    _dns(provisioning.get("dns"), "network.provisioning.dns", False, errors)

    acceptance = _unknown(
        provisioning.get("acceptance"), ACCEPTANCE_KEYS, "network.provisioning.acceptance", errors
    )
    test_host = acceptance.get("test_host")
    if test_host not in (None, ""):
        host = _ip(test_host, "network.provisioning.acceptance.test_host", SCHEMA_MODE, errors)
        if host is not None and provisioning_net is not None and host not in provisioning_net:
            errors.append("the acceptance test host must be inside the provisioning subnet")
        if host is not None and start is not None and end is not None and start <= host <= end:
            errors.append("the acceptance test host must be outside the dynamic DHCP pool")
        if host is not None and controller is not None and host == controller:
            errors.append("the acceptance test host must not be the controller address")


def _validate_deployment(deployment: Any, mode: str, errors: list[str]) -> None:
    deployment = _unknown(deployment, DEPLOYMENT_KEYS, "deployment", errors)
    hostname = deployment.get("hostname")
    if not isinstance(hostname, str) or not _HOSTNAME.match(hostname):
        errors.append("deployment.hostname must be a single DNS label")
    for key in ("locale", "keyboard_layout"):
        if not isinstance(deployment.get(key), str) or not deployment.get(key):
            errors.append(f"deployment.{key} must be a non-empty string")
    if not isinstance(deployment.get("timezone"), str) or not _TIMEZONE.match(str(deployment.get("timezone"))):
        errors.append("deployment.timezone must be an IANA timezone name")
    variant = deployment.get("keyboard_variant", "")
    if not isinstance(variant, str) or not re.fullmatch(r"[A-Za-z0-9_-]*", variant):
        errors.append("deployment.keyboard_variant must be an XKB variant name")
    admin = deployment.get("admin_user")
    if not isinstance(admin, str) or not _USER.match(admin):
        errors.append("deployment.admin_user must be a local account name")
    if admin in {"root", "maas", "postgres"}:
        errors.append("deployment.admin_user must not be root, maas, or postgres")

    keys = deployment.get("ssh_authorized_keys")
    if not isinstance(keys, list):
        errors.append("deployment.ssh_authorized_keys must be a list")
    elif mode == INSTALL_MODE and not keys:
        errors.append("at least one SSH public key is required before installation")
    elif isinstance(keys, list):
        for index, key in enumerate(keys):
            if not isinstance(key, str) or not _SSH_PUBLIC.match(key.strip()):
                errors.append(f"deployment.ssh_authorized_keys[{index}] is not an SSH public key")

    disk = _unknown(deployment.get("disk"), DISK_KEYS, "deployment.disk", errors)
    _validate_disk(disk, mode, errors)

    ubuntu = _unknown(deployment.get("ubuntu"), UBUNTU_KEYS, "deployment.ubuntu", errors)
    if ubuntu.get("codename") != "noble" or ubuntu.get("release") != "24.04.5":
        errors.append("deployment.ubuntu must select Ubuntu Server 24.04.5 (noble)")
    if ubuntu.get("iso_name") != "ubuntu-24.04.5-live-server-amd64.iso":
        errors.append("deployment.ubuntu.iso_name is not the pinned live-server ISO")
    sha = str(ubuntu.get("sha256", "")).lower()
    if not _SHA256.match(sha):
        errors.append("deployment.ubuntu.sha256 must be a SHA-256 hex digest")
    if not str(ubuntu.get("base_url", "")).startswith("https://releases.ubuntu.com/"):
        errors.append("deployment.ubuntu.base_url must be an official Ubuntu release URL")

    maas = _unknown(deployment.get("maas"), MAAS_KEYS, "deployment.maas", errors)
    if maas.get("channel") != MAAS_CHANNEL:
        errors.append(
            f"deployment.maas.channel must be {MAAS_CHANNEL}, the release verified "
            "for Ubuntu 24.04 and PostgreSQL 16"
        )
    if maas.get("mode") != MAAS_MODE:
        errors.append("deployment.maas.mode must be region+rack")
    if not isinstance(maas.get("admin_username"), str) or not _USER.match(str(maas.get("admin_username"))):
        errors.append("deployment.maas.admin_username must be a local account name")
    email = maas.get("admin_email")
    if not isinstance(email, str) or "@" not in email or email.startswith("@"):
        errors.append("deployment.maas.admin_email must be an email address")
    if maas.get("image_release") != "noble":
        errors.append("deployment.maas.image_release must be noble")

    postgres = _unknown(deployment.get("postgresql"), POSTGRES_KEYS, "deployment.postgresql", errors)
    if postgres.get("major") != POSTGRES_MAJOR:
        errors.append("deployment.postgresql.major must be 16")
    for key in ("database", "user"):
        if not isinstance(postgres.get(key), str) or not _USER.match(str(postgres.get(key))):
            errors.append(f"deployment.postgresql.{key} must be a PostgreSQL identifier")

    backup = _unknown(deployment.get("backup"), BACKUP_KEYS, "deployment.backup", errors)
    directory = backup.get("directory")
    if not isinstance(directory, str) or not directory.startswith("/"):
        errors.append("deployment.backup.directory must be an absolute path")
    target = backup.get("offhost_target")
    if target not in (None, "") and (
        not isinstance(target, str) or " " in target or target.startswith("-")
    ):
        errors.append("deployment.backup.offhost_target must be an rsync destination or null")
    retain = backup.get("retain")
    if not isinstance(retain, int) or isinstance(retain, bool) or not 1 <= retain <= 365:
        errors.append("deployment.backup.retain must be an integer from 1 to 365")


def _validate_disk(disk: dict[str, Any], mode: str, errors: list[str]) -> None:
    serial = disk.get("serial")
    wwn = disk.get("wwn")
    by_id = disk.get("by_id")
    model = disk.get("model")
    confirm = disk.get("confirm_erase")
    if confirm is not False and confirm is not True:
        errors.append("deployment.disk.confirm_erase must be true or false")
    for label, value in (("serial", serial), ("wwn", wwn), ("model", model)):
        if value is None:
            continue
        if not isinstance(value, str) or not value.strip() or len(value) > 128:
            errors.append(f"deployment.disk.{label} must be a short identifier string")
        elif value.strip() in {"sda", "/dev/sda", "nvme0n1", "/dev/nvme0n1"} or "largest" in value.lower():
            errors.append(f"deployment.disk.{label} must not select a disk by unstable name or size")
    if by_id is not None:
        if not isinstance(by_id, str) or not _BY_ID.match(by_id):
            errors.append("deployment.disk.by_id must be a /dev/disk/by-id/ path")
    identifiers = [value for value in (serial, wwn, by_id) if value not in (None, "")]
    if mode == INSTALL_MODE:
        if confirm is not True:
            errors.append(
                "deployment.disk.confirm_erase must be true to acknowledge which disk will be erased"
            )
        if not identifiers:
            errors.append(
                "deployment.disk requires serial, wwn, or by_id. "
                "The largest disk and /dev/sda are never selected automatically"
            )
        if model and not identifiers:
            errors.append("deployment.disk.model cannot identify a disk by itself")


def _port(value: Any, location: str, port_max: int, mode: str, errors: list[str]) -> int | None:
    if value is None:
        if mode == INSTALL_MODE:
            errors.append(f"{location} is required before installation")
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        errors.append(f"{location} must be an integer port number")
        return None
    if not 1 <= value <= port_max:
        errors.append(f"{location} must be a GS724Tv4 port from 1 to {port_max}")
        return None
    return value


def _vlan(value: Any, location: str, errors: list[str], allow_one: bool) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        errors.append(f"{location} must be an integer VLAN ID")
        return None
    lower = 1 if allow_one else 2
    if not lower <= value <= VLAN_MAX:
        errors.append(f"{location} must be from {lower} to {VLAN_MAX}")
        return None
    return value


def _reserved_vlan(vlan_id: int | None, errors: list[str]) -> None:
    if vlan_id is None:
        return
    if vlan_id in RESERVED_PROVISIONING_VLANS:
        errors.append(
            f"VLAN {vlan_id} is predefined on GS724Tv4 and cannot be the provisioning VLAN"
        )


def _mac(value: Any, location: str, errors: list[str]) -> None:
    if not isinstance(value, str) or not _MAC.match(value.strip().lower()):
        errors.append(f"{location} must be a colon-separated MAC address")
        return
    first = int(value.strip().split(":", 1)[0], 16)
    if first & 0x01:
        errors.append(f"{location} must be a unicast MAC address")
    if value.strip().lower() == "00:00:00:00:00:00":
        errors.append(f"{location} must not be the all-zero MAC address")


def _ip(value: Any, location: str, mode: str, errors: list[str]) -> ipaddress.IPv4Address | None:
    if value is None:
        if mode == INSTALL_MODE:
            errors.append(f"{location} is required before installation")
        return None
    try:
        address = ipaddress.ip_address(str(value))
    except ValueError:
        errors.append(f"{location} must be an IPv4 address")
        return None
    if not isinstance(address, ipaddress.IPv4Address):
        errors.append(f"{location} must be an IPv4 address")
        return None
    return address


def _cidr(
    value: Any, location: str, mode: str, errors: list[str], host: bool
) -> ipaddress.IPv4Network | None:
    if value is None:
        if mode == INSTALL_MODE:
            errors.append(f"{location} is required before installation")
        return None
    try:
        network = ipaddress.ip_network(str(value), strict=not host)
    except ValueError:
        errors.append(f"{location} must be an IPv4 CIDR")
        return None
    if not isinstance(network, ipaddress.IPv4Network):
        errors.append(f"{location} must be an IPv4 CIDR")
        return None
    if network.prefixlen < 8 or network.prefixlen > 30:
        errors.append(f"{location} prefix length must be from 8 to 30")
        return None
    if host and network.prefixlen == 32:
        errors.append(f"{location} must include a subnet prefix, not a /32 host route")
    return network


def _dns(value: Any, location: str, required: bool, errors: list[str]) -> None:
    if not isinstance(value, list):
        errors.append(f"{location} must be a list")
        return
    if required and not value:
        errors.append(f"{location} must contain at least one resolver")
    for index, item in enumerate(value):
        _ip(item, f"{location}[{index}]", SCHEMA_MODE, errors)
