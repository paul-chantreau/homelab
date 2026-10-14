#!/bin/bash
# Local health report for PostgreSQL, MAAS, DHCP state, disk space, and secrets.
set -euo pipefail
set +x

ROOT="${MAAS_DEPLOY_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
export PYTHONPATH="${ROOT}${PYTHONPATH:+:$PYTHONPATH}"
python3 - <<'PY'
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

from maas_deploy.healthcheck import evaluate

def active(unit: str) -> bool:
    completed = subprocess.run(["systemctl", "is-active", "--quiet", unit])
    return completed.returncode == 0

status = subprocess.run(["maas", "status"], text=True, capture_output=True)
text = (status.stdout or "") + (status.stderr or "")
region = any(line.split()[:1] == ["regiond"] and line.split()[-1].lower() == "running" for line in text.splitlines() if line.split())
rack = any(line.split()[:1] == ["rackd"] and line.split()[-1].lower() == "running" for line in text.splitlines() if line.split())
dhcp_running = any(line.split()[:1] == ["dhcpd"] and line.split()[-1].lower() == "running" for line in text.splitlines() if line.split())
secrets = Path("/etc/maas-provision/secrets.env")
mode = stat.S_IMODE(secrets.stat().st_mode) if secrets.exists() else 0
usage = shutil.disk_usage("/")
snapshot = {
    "postgresql_active": active("postgresql"),
    "region_active": region,
    "rack_active": rack,
    # Activation refuses the management VLAN. A stopped dhcpd means neither VLAN is serving DHCP.
    "management_dhcp_on": False if not dhcp_running else None,
    "provisioning_dhcp_on": dhcp_running,
    "provisioning_dhcp_expected": Path("/var/lib/maas-provision/dhcp-active").exists(),
    "disk_used_percent": round(usage.used * 100 / usage.total, 1),
    "secrets_mode": mode,
}
report = evaluate(snapshot)
print(json.dumps(report, indent=2))
sys.exit(0 if report["outcome"] == "pass" else 1)
PY
