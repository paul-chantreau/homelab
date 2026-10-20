"""Interpret local service and disk observations."""

from __future__ import annotations

from typing import Any


def evaluate(snapshot: dict[str, Any]) -> dict[str, Any]:
    checks: dict[str, str] = {}
    checks["postgresql"] = "pass" if snapshot.get("postgresql_active") else "fail"
    checks["maas_region"] = "pass" if snapshot.get("region_active") else "fail"
    checks["maas_rack"] = "pass" if snapshot.get("rack_active") else "fail"
    checks["management_dhcp_disabled"] = (
        "pass" if snapshot.get("management_dhcp_on") is False else "fail"
    )
    if snapshot.get("provisioning_dhcp_expected") is True:
        checks["provisioning_dhcp"] = "pass" if snapshot.get("provisioning_dhcp_on") is True else "fail"
    else:
        checks["provisioning_dhcp"] = "pass" if snapshot.get("provisioning_dhcp_on") is False else "fail"
    usage = snapshot.get("disk_used_percent")
    checks["disk_space"] = "pass" if isinstance(usage, (int, float)) and usage < 80 else "fail"
    checks["secrets_mode"] = "pass" if snapshot.get("secrets_mode") == 0o600 else "fail"
    outcome = "pass" if all(value == "pass" for value in checks.values()) else "fail"
    return {"outcome": outcome, "checks": checks}


def collect(config, secrets_path, state_dir):
    """Collect service state and MAAS configuration rather than infer VLANs."""
    import ipaddress
    import shutil
    import stat
    from maas_deploy.facts import parse_maas_status
    from maas_deploy.maas_network import controller_vlan, subnet_vlan
    from maas_deploy.runtime import _run, inventory
    from maas_deploy.secrets import load

    secrets = load(secrets_path)
    profile = config.deployment['maas']['admin_username']
    _run(['maas', 'login', profile, 'http://127.0.0.1:5240/MAAS/', secrets['MAAS_API_KEY']])
    current = inventory(profile)
    provisioning = controller_vlan(config.network, current)
    management_cidr = ipaddress.ip_interface(config.network['management']['address']).network
    management = [item for item in current['subnets'] if ipaddress.ip_network(item['cidr']) == management_cidr]
    management_dhcp = subnet_vlan(management[0], current).get('dhcp_on') if len(management) == 1 else None
    status = _run(['maas', 'status'], check=False)
    services = parse_maas_status(status.stdout) if status.returncode == 0 else {}
    usage = shutil.disk_usage('/')
    return {
        **services,
        'postgresql_active': _run(['pg_isready', '-h', '127.0.0.1', '-p', '5432'], check=False).returncode == 0,
        'management_dhcp_on': management_dhcp,
        'provisioning_dhcp_on': provisioning.get('dhcp_on'),
        'provisioning_dhcp_expected': (state_dir / 'dhcp-active').exists(),
        'disk_used_percent': usage.used * 100 / usage.total,
        'secrets_mode': stat.S_IMODE(secrets_path.stat().st_mode),
    }
