#!/bin/bash
# Restore a MAAS PostgreSQL custom-format dump. This replaces the MAAS database.
set -euo pipefail
set +x

if [[ "${1:-}" != "--confirm" || "${2:-}" != "RESTORE" || -z "${3:-}" ]]; then
  echo "Usage: sudo maas-restore --confirm RESTORE /var/backups/maas/maas-TIMESTAMP.dump" >&2
  echo "Stop and think. This replaces the current MAAS database." >&2
  exit 2
fi
DUMP="$3"
if [[ ! -f "${DUMP}" ]]; then
  echo "Dump not found: ${DUMP}" >&2
  exit 1
fi

ROOT="${MAAS_DEPLOY_ROOT:-/opt/maas-deploy}"
export PYTHONPATH="${ROOT}${PYTHONPATH:+:$PYTHONPATH}"
python3 - "${DUMP}" <<'PY'
import subprocess
import sys
from pathlib import Path
import os
from maas_deploy.config import load_config

dump = Path(sys.argv[1])
root = Path(os.environ.get("MAAS_DEPLOY_ROOT", "/opt/maas-deploy"))
config = load_config(root / "config/network.yaml", root / "config/deployment.yaml")
database = config.deployment["postgresql"]["database"]
owner = config.deployment["postgresql"]["user"]
subprocess.run(["snap", "stop", "maas"], check=True)
subprocess.run(["sudo", "-u", "postgres", "psql", "-v", "ON_ERROR_STOP=1", "-c", f"DROP DATABASE IF EXISTS {database};"], check=True)
subprocess.run(["sudo", "-u", "postgres", "psql", "-v", "ON_ERROR_STOP=1", "-c", f"CREATE DATABASE {database} OWNER {owner};"], check=True)
subprocess.run(["sudo", "-u", "postgres", "pg_restore", "--dbname", database, "--no-owner", str(dump)], check=True)
subprocess.run(["snap", "start", "maas"], check=True)
print("Restored and started MAAS.")
PY
