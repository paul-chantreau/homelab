"""Apply PostgreSQL and MAAS configuration on the controller.

These functions are the production path. They refuse to drop an existing
database or to reset an existing MAAS administrator.
"""

from __future__ import annotations

import json
import subprocess
import time
from urllib.request import urlopen
from urllib.error import URLError

from maas_deploy.facts import parse_maas_status
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
    current_config = {}
    if listed.returncode == 0:
        config_output = _run(["maas", "config", "--show", "--parsable"]).stdout
        current_config = dict(line.split("=", 1) for line in config_output.splitlines() if "=" in line)
        if current_config.get("mode") not in {"none", "region+rack"}:
            raise SetupError("Existing MAAS mode is incompatible; refusing to reinitialize")
    initialized = current_config.get("mode") == "region+rack"
    admin_name = config.deployment["maas"]["admin_username"]
    admin_exists = False
    if initialized:
        _wait_for_maas()
        admin_exists = _run(["maas", "apikey", "--username", admin_name], check=False).returncode == 0
    if initialized and current_config.get("maas_url") != url:
        _run(["maas", "config", "--maas-url", url])
        _wait_for_maas()
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
        if name == "login":
            _login(config, secrets_path)
            continue
        command = list(step["command"])
        if name == "init":
            _run(command)
            _wait_for_maas()
            continue
        _run(command)
    return "changed" if not initialized or not admin_exists or current_config.get("maas_url") != url else "unchanged"


def _login(config: LoadedConfig, secrets_path: Path) -> None:
    username = config.deployment["maas"]["admin_username"]
    keys = _run(["maas", "apikey", "--username", username]).stdout.splitlines()
    if not any(line.strip() for line in keys):
        keys = _run(["maas", "apikey", "--username", username, "--generate"]).stdout.splitlines()
    key = next((line.strip() for line in keys if line.strip()), "")
    if not key:
        raise SetupError("MAAS returned no API key for the configured administrator")
    store_api_key(secrets_path, key)
    _run(["maas", "login", username, "http://127.0.0.1:5240/MAAS/", key])


def _wait_for_maas() -> None:
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        result = _run(["maas", "status"], check=False)
        status = parse_maas_status(result.stdout)
        if result.returncode == 0 and status["region_active"] and status["rack_active"]:
            try:
                with urlopen("http://127.0.0.1:5240/MAAS/api/2.0/version/", timeout=5) as response:
                    version = json.load(response)
                if version.get("version"):
                    return
            except (URLError, OSError, ValueError):
                pass
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
    racks = _maas_json(profile, "rack-controllers", "read")
    interfaces = []
    if len(racks) == 1 and racks[0].get("system_id"):
        interfaces = _maas_json(profile, "interfaces", "read", racks[0]["system_id"])
    return {
        "interfaces": interfaces,
        "subnets": _maas_json(profile, "subnets", "read"),
        "vlans": vlans,
        "ipranges": _maas_json(profile, "ipranges", "read"),
        "racks": racks,
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
    secrets = load(secrets_path)
    profile = config.deployment["maas"]["admin_username"]
    _run(["maas", "login", profile, "http://127.0.0.1:5240/MAAS/", secrets["MAAS_API_KEY"]])
    release = config.deployment["maas"]["image_release"]
    resources = _image_resources(profile, release)
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
    resources = _image_resources(profile, release)
    if not _image_synced(resources, release):
        raise SetupError(f"Ubuntu {release} image is not synced")
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("synced\n", encoding="utf-8")
    return "synced"


def _image_resources(profile: str, release: str) -> list[dict]:
    resources = _maas_json(profile, "boot-resources", "read")
    return [
        _maas_json(profile, "boot-resource", "read", str(resource["id"]))
        for resource in resources
        if resource.get("name") == f"ubuntu/{release}" and str(resource.get("architecture", "")).startswith("amd64/")
    ]


def _image_synced(resources: Any, release: str) -> bool:
    if not isinstance(resources, list):
        return False
    for resource in resources:
        if resource.get("type") != "Synced" or resource.get("name") != f"ubuntu/{release}":
            continue
        if not str(resource.get("architecture", "")).startswith("amd64/"):
            continue
        for resource_set in (resource.get("sets") or {}).values():
            files = resource_set.get("files") or {}
            if resource_set.get("complete") is True and files and all(f.get("complete") is True for f in files.values()):
                return True
    return False
