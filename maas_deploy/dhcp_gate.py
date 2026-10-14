"""Decide whether MAAS DHCP may be enabled or must be stopped."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from maas_deploy.acceptance import PASS, CRITICAL_CHECKS


def activation_phrase(vlan_id: int) -> str:
    return f"ACTIVATE DHCP ON VLAN {vlan_id}"


def deactivation_phrase(vlan_id: int) -> str:
    return f"DEACTIVATE DHCP ON VLAN {vlan_id}"


def emergency_phrase(vlan_id: int) -> str:
    return f"EMERGENCY DEACTIVATE DHCP ON VLAN {vlan_id}"


def assess_activation(
    report: dict[str, Any],
    config_hash: str,
    now: datetime | None = None,
    max_age_seconds: int = 7200,
) -> tuple[bool, str]:
    """Return whether DHCP activation is allowed, and why."""
    if not isinstance(report, dict):
        return False, "validation report is missing"
    if report.get("config_hash") != config_hash:
        return False, "the network configuration changed after validation"
    if report.get("outcome") != PASS or report.get("blocks_dhcp") is not False:
        return False, f"validation outcome is {report.get('outcome')}"
    checks = report.get("checks") or {}
    for name in CRITICAL_CHECKS:
        status = (checks.get(name) or {}).get("status")
        if status != PASS:
            return False, f"{name} is {status or 'missing'}"
    generated = report.get("generated_at")
    if not isinstance(generated, str):
        return False, "validation report has no timestamp"
    try:
        moment = datetime.fromisoformat(generated)
    except ValueError:
        return False, "validation report timestamp is invalid"
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    age = current - moment.astimezone(timezone.utc)
    if age.total_seconds() < 0 or age.total_seconds() > max_age_seconds:
        return False, "validation report is older than the allowed window"
    return True, "validation passed and matches the current configuration"
