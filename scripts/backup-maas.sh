#!/bin/bash
# Dump the MAAS PostgreSQL database and retain a local history.
set -euo pipefail
set +x

ROOT="${MAAS_DEPLOY_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
export PYTHONPATH="${ROOT}${PYTHONPATH:+:$PYTHONPATH}"
python3 - <<'PY'
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from maas_deploy.backup import dump_command, rsync_command, select_expired
from maas_deploy.config import load_config

root = Path(os.environ.get("MAAS_DEPLOY_ROOT", "/opt/maas-deploy"))
config = load_config(root / "config/network.yaml", root / "config/deployment.yaml")
backup = config.deployment["backup"]
database = config.deployment["postgresql"]["database"]
destination_dir = Path(backup["directory"])
destination_dir.mkdir(parents=True, exist_ok=True)
stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
dump = destination_dir / f"maas-{stamp}.dump"
subprocess.run(dump_command(database, dump), check=True)
os.chmod(dump, 0o600)
expired = select_expired(sorted(destination_dir.glob("maas-*.dump")), int(backup["retain"]))
for path in expired:
    path.unlink()
target = backup.get("offhost_target")
if target:
    subprocess.run(rsync_command(dump, target), check=True)
print(dump)
PY
