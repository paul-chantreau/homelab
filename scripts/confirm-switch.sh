#!/bin/bash
# Record that an administrator checked the GS724T web interface for this configuration.
set -euo pipefail
set +x

SCRIPT_PATH="$(readlink -f -- "${BASH_SOURCE[0]}")"
ROOT="${MAAS_DEPLOY_ROOT:-$(cd "$(dirname "${SCRIPT_PATH}")/.." && pwd)}"
export MAAS_DEPLOY_ROOT="$ROOT"
export MAAS_STATE_DIR="${MAAS_STATE_DIR:-/var/lib/maas-provision}"
export PYTHONPATH="${ROOT}${PYTHONPATH:+:$PYTHONPATH}"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root: sudo maas-network-confirm-switch" >&2
  exit 1
fi

python3 - "${ROOT}/config/network.yaml" <<'PY'
import sys
from pathlib import Path
import yaml
from maas_deploy.config import HARDWARE_REVISION
doc = yaml.safe_load(Path(sys.argv[1]).read_text(encoding="utf-8"))["network"]
management = doc["management"]["vlan_id"]
provisioning = doc["provisioning"]["vlan_id"]
print(f"Confirm these {HARDWARE_REVISION} settings in the switch web interface before continuing.")
print(f"  Router port: unchanged management VLAN {management}; excluded from VLAN {provisioning}")
print(f"  MAAS port: management VLAN {management} untagged, VLAN {provisioning} tagged, PVID {management}")
print(f"  Node ports: VLAN {provisioning} untagged only, PVID {provisioning}")
print("Identify the actual connected ports; README section G provides an optional local worksheet.")
print("Do not continue from memory. Read the VLAN Membership and Port PVID pages.")
PY

eval "$(python3 - <<'PY'
from maas_deploy.config import HARDWARE_REVISION, load_config
import os
from pathlib import Path
root = Path(os.environ["MAAS_DEPLOY_ROOT"])
config = load_config(root / "config/network.yaml", root / "config/deployment.yaml")
revision = HARDWARE_REVISION
vlan = config.network["provisioning"]["vlan_id"]
print(f"EXPECTED='CONFIRM GS724T {revision} VLAN {vlan}'")
PY
)"

echo "Type exactly: ${EXPECTED}"
read -r MAAS_SWITCH_CONFIRM
export MAAS_SWITCH_CONFIRM
python3 -m maas_deploy confirm-switch
