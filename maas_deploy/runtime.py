"""Apply PostgreSQL and MAAS configuration on the controller.

These functions are the production path. They refuse to drop an existing
database or to reset an existing MAAS administrator.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any

from maas_deploy.config import LoadedConfig
from maas_deploy.maas_network import plan_initial, render_maas_command
from maas_deploy.maas_setup import advertised_url, build_steps, database_uri
from maas_deploy.postgres_setup import plan
from maas_deploy.redact import redact
from maas_deploy.secrets import ensure, load, store_api_key


class SetupError(Exception):
    """A local provisioning step failed."""


def _run(command: list[str], *, input_text: str | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        command,
        input=input_text,
        text=True,
        capture_output=True,
        check=False,
    )
    if check and completed.returncode != 0:
        detail = redact((completed.stderr or "") + (completed.stdout or ""))
        raise SetupError(f"command failed ({completed.returncode}): {' '.join(command[:4])}\n{detail}")
    return completed


def _psql(sql: str) -> str:
    completed = _run(["sudo", "-u", "postgres", "psql", "-v", "ON_ERROR_STOP=1", "-tAc", sql])
    return completed.stdout.strip()


def apply_postgres(config: LoadedConfig, secrets_path: Path) -> str:
    ensure(secrets_path)
    secrets = load(secrets_path)
    postgres = config.deployment["postgresql"]
    hba_path = Path(f"/etc/postgresql/{postgres['major']}/main/pg_hba.conf")
    if not hba_path.exists():
        raise SetupError(f"PostgreSQL configuration was not found at {hba_path}")
    role_exists = _psql(f"SELECT 1 FROM pg_roles WHERE rolname='{postgres['user']}'") == "1"
    database_exists = _psql(f"SELECT 1 FROM pg_database WHERE datname='{postgres['database']}'") == "1"
    current_hba = hba_path.read_text(encoding="utf-8")
    planned = plan(
        {"role_exists": role_exists, "database_exists": database_exists, "hba": current_hba},
        postgres["database"],
        postgres["user"],
        secrets["POSTGRES_PASSWORD"],
    )
    changed = False
    if planned["role_sql"]:
        _psql(planned["role_sql"])
        changed = True
    if planned["database_sql"]:
        _psql(planned["database_sql"])
        changed = True
    if current_hba != planned["hba"]:
        hba_path.write_text(planned["hba"], encoding="utf-8")
        _run(["systemctl", "reload", "postgresql"])
        changed = True
    return "changed" if changed else "unchanged"


def apply_maas(config: LoadedConfig, secrets_path: Path) -> str:
    ensure(secrets_path)
    secrets = load(secrets_path)
    postgres = config.deployment["postgresql"]
    uri = database_uri(postgres["user"], secrets["POSTGRES_PASSWORD"], postgres["database"])
    url = advertised_url(str(config.network["provisioning"]["controller_address"]))
    listed = _run(["snap", "list", "maas"], check=False)
    channel = None
    if listed.returncode == 0:
        info = _run(["snap", "info", "maas"]).stdout
        for line in info.splitlines():
            if line.startswith("tracking:"):
                channel = line.split(":", 1)[1].strip()
    initialized = _run(["maas", "status"], check=False).returncode == 0
    admin_name = config.deployment["maas"]["admin_username"]
    admin_exists = False
    if initialized:
        admin_exists = _run(["maas", "apikey", "--username", admin_name], check=False).returncode == 0
    steps = build_steps(
        {
            "snap_installed": listed.returncode == 0,
            "channel": channel,
            "initialized": initialized,
            "admin_exists": admin_exists,
        },
        config.deployment,
        uri,
        url,
    )
    for step in steps:
        name = step["name"]
        if name == "disable-timesyncd":
            _run(["systemctl", "disable", "--now", "systemd-timesyncd"], check=False)
            continue
        if name == "createadmin":
            _run(
                [
                    *step["command"],
                    "--password",
                    secrets["MAAS_ADMIN_PASSWORD"],
                ]
            )
            continue
        if name == "login-and-set-url":
            _login_and_set_url(config, secrets_path, secrets, url)
            continue
        command = list(step["command"])
        if name == "init":
            _run(command)
            _wait_for_maas()
            continue
        _run(command)
    return "ready"


def _login_and_set_url(config: LoadedConfig, secrets_path: Path, secrets: dict[str, str], url: str) -> None:
    username = config.deployment["maas"]["admin_username"]
    key = _run(["maas", "apikey", "--username", username]).stdout.strip()
    store_api_key(secrets_path, key)
    _run(["maas", "login", username, "http://127.0.0.1:5240/MAAS/", key])
    _run(["maas", username, "maas", "set-config", f"name=maas_url", f"value={url}"])


def _wait_for_maas() -> None:
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        if _run(["maas", "status"], check=False).returncode == 0:
            return
        time.sleep(5)
    raise SetupError("MAAS did not become ready after initialization")


def _maas_json(profile: str, *args: str) -> Any:
    completed = _run(["maas", profile, *args])
    if not completed.stdout.strip():
        return []
    return json.loads(completed.stdout)


def inventory(profile: str) -> dict[str, Any]:
    fabrics = _maas_json(profile, "fabrics", "read")
    vlans = []
    for fabric in fabrics:
        for vlan in fabric.get("vlans") or []:
            item = dict(vlan)
            item["fabric_id"] = fabric.get("id")
            vlans.append(item)
    return {
        "subnets": _maas_json(profile, "subnets", "read"),
        "vlans": vlans,
        "ipranges": _maas_json(profile, "ipranges", "read"),
        "racks": _maas_json(profile, "rack-controllers", "read"),
    }


def apply_network(config: LoadedConfig, secrets_path: Path) -> str:
    secrets = load(secrets_path)
    profile = config.deployment["maas"]["admin_username"]
    _run(["maas", "login", profile, "http://127.0.0.1:5240/MAAS/", secrets["MAAS_API_KEY"]])
    deadline = time.monotonic() + 600
    last_error = "provisioning VLAN was not discovered"
    while time.monotonic() < deadline:
        current = inventory(profile)
        try:
            actions = plan_initial(config.network, current)
        except Exception as exc:  # noqa: BLE001 - retry until the rack reports the VLAN
            last_error = str(exc)
            time.sleep(10)
            continue
        for action in actions:
            if action["op"] == "subnet-create":
                created = json.loads(
                    _run(render_maas_command(profile, action)).stdout or "{}"
                )
                replacement = str(created.get("id", ""))
                for pending in actions:
                    if pending.get("id") == "$provisioning":
                        pending["id"] = replacement
                    args = pending.get("args") or {}
                    if args.get("subnet") == "$provisioning":
                        args["subnet"] = replacement
                continue
            _run(render_maas_command(profile, action))
        return "ready"
    raise SetupError(last_error)


def apply_images(config: LoadedConfig, secrets_path: Path, marker: Path) -> str:
    if marker.exists():
        return "already-synced"
    secrets = load(secrets_path)
    profile = config.deployment["maas"]["admin_username"]
    _run(["maas", "login", profile, "http://127.0.0.1:5240/MAAS/", secrets["MAAS_API_KEY"]])
    release = config.deployment["maas"]["image_release"]
    resources = _maas_json(profile, "boot-resources", "read")
    if _image_synced(resources, release):
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("synced\n", encoding="utf-8")
        return "already-synced"
    sources = _maas_json(profile, "boot-sources", "read")
    if not sources:
        raise SetupError("MAAS has no boot source")
    source_id = str(sources[0]["id"])
    selections = _maas_json(profile, "boot-source-selections", "read", source_id)
    if not any(item.get("release") == release and item.get("os") == "ubuntu" for item in selections):
        _run(
            [
                "maas",
                profile,
                "boot-source-selections",
                "create",
                source_id,
                "os=ubuntu",
                f"release={release}",
                "arches=amd64",
                "subarches=*",
                "labels=*",
            ]
        )
    _run(["maas", profile, "boot-resources", "import"])
    deadline = time.monotonic() + 3600
    while time.monotonic() < deadline:
        state = _run(["maas", profile, "boot-resources", "is-importing"], check=False)
        if state.returncode == 0 and state.stdout.strip().lower() in {"false", "no"}:
            break
        time.sleep(15)
    else:
        raise SetupError("Ubuntu image import did not finish")
    resources = _maas_json(profile, "boot-resources", "read")
    if not _image_synced(resources, release):
        raise SetupError(f"Ubuntu {release} image is not synced")
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("synced\n", encoding="utf-8")
    return "synced"


def _image_synced(resources: Any, release: str) -> bool:
    if not isinstance(resources, list):
        return False
    for resource in resources:
        name = str(resource.get("name", ""))
        if resource.get("type") == "Synced" and f"ubuntu/{release}" in name:
            return True
    return False
