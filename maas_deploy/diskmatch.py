"""Match the installation disk by stable identifiers.

The largest disk is never a candidate. Kernel names such as /dev/sda are never
accepted as selectors.
"""

from __future__ import annotations

from typing import Any


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
