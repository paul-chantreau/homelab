"""Plan the local MAAS snap installation.

Applying the plan requires the server. Tests inspect the plan without contacting
the snap store or PostgreSQL.
"""

from __future__ import annotations

from typing import Any

from maas_deploy.config import MAAS_CHANNEL, MAAS_MODE


def database_uri(user: str, password: str, database: str) -> str:
    return f"postgres://{user}:{password}@127.0.0.1:5432/{database}"


def advertised_url(controller_address: str) -> str:
    return f"http://{controller_address}:5240/MAAS/"


def build_steps(state: dict[str, Any], deployment: dict[str, Any], uri: str, url: str) -> list[dict[str, Any]]:
    """Return installation steps.

    An initialized MAAS is left in place. Administrative passwords are not reset
    when the admin user already exists.
    """
    if deployment["maas"]["channel"] != MAAS_CHANNEL or deployment["maas"]["mode"] != MAAS_MODE:
        raise ValueError("MAAS channel or mode is not the pinned production selection")
    if state.get("snap_installed") and state.get("channel") not in {None, MAAS_CHANNEL}:
        raise ValueError(
            f"MAAS is installed from {state.get('channel')}. Refusing to change channel automatically"
        )
    steps: list[dict[str, Any]] = [{"name": "disable-timesyncd"}]
    if not state.get("snap_installed"):
        steps.append(
            {
                "name": "install-snap",
                "command": ["snap", "install", "maas", f"--channel={MAAS_CHANNEL}"],
            }
        )
    steps.append({"name": "hold-snap-refresh", "command": ["snap", "refresh", "--hold=forever", "maas"]})
    if not state.get("initialized"):
        steps.append(
            {
                "name": "init",
                "command": [
                    "maas",
                    "init",
                    MAAS_MODE,
                    "--database-uri",
                    uri,
                    "--maas-url",
                    url,
                ],
                "secret_args": True,
            }
        )
    if not state.get("admin_exists"):
        steps.append(
            {
                "name": "createadmin",
                "command": [
                    "maas",
                    "createadmin",
                    "--username",
                    deployment["maas"]["admin_username"],
                    "--email",
                    deployment["maas"]["admin_email"],
                ],
                "secret_stdin": True,
            }
        )
    steps.append({"name": "login-and-set-url", "url": url})
    return steps
