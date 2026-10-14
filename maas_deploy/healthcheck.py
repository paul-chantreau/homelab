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
