"""Display the initial MAAS login details directly on the local console."""

from __future__ import annotations

import ipaddress
import os
from pathlib import Path

from maas_deploy.config import LoadedConfig
from maas_deploy.secrets import load


def display_access(config: LoadedConfig, secrets_path: Path, console_path: Path = Path("/dev/tty1")) -> None:
    """Write credentials to a terminal without routing them through service logs."""
    descriptor = os.open(console_path, os.O_WRONLY | os.O_NOCTTY)
    with os.fdopen(descriptor, "w", encoding="utf-8") as console:
        if not os.isatty(console.fileno()):
            raise OSError(f"MAAS access details require a terminal: {console_path}")
        values = load(secrets_path)
        address = ipaddress.ip_interface(config.network["management"]["address"]).ip
        username = config.deployment["maas"]["admin_username"]
        console.write(
            "\nMAAS installation completed.\n\n"
            "Access MAAS with the following link:\n"
            f"http://{address}:5240/MAAS/\n\n"
            f"Username: {username}\n"
            f"Password: {values['MAAS_ADMIN_PASSWORD']}\n\n"
        )
