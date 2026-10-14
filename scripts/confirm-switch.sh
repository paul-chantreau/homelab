#!/bin/bash
# Record that an administrator checked the GS724T web interface for this configuration.
set -euo pipefail
set +x

ROOT="${MAAS_DEPLOY_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
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
doc = yaml.safe_load(Path(sys.argv[1]).read_text(encoding="utf-8"))["network"]
switch = doc["switch"]
print("Confirm these GS724T settings in the switch web interface before continuing.")
print(f"  Hardware revision: {switch['hardware_revision']}")
print(f"  Firmware: {switch['firmware_version']}")
print(f"  Router port: {switch['router_port']}  (unchanged management LAN)")
print(f"  MAAS port: {switch['maas_port']}  (management untagged, VLAN {doc['provisioning']['vlan_id']} tagged)")
print(f"  Provisioning ports: {switch['provisioning_ports']}  (VLAN {doc['provisioning']['vlan_id']} untagged, PVID {doc['provisioning']['vlan_id']})")
print("Do not continue from memory. Read the VLAN Membership and Port PVID pages.")
PY

eval "$(python3 - <<'PY'
from maas_deploy.config import load_config
import os
from pathlib import Path
root = Path(os.environ["MAAS_DEPLOY_ROOT"])
config = load_config(root / "config/network.yaml", root / "config/deployment.yaml")
revision = config.network["switch"]["hardware_revision"]
vlan = config.network["provisioning"]["vlan_id"]
print(f"EXPECTED='CONFIRM GS724T {revision} VLAN {vlan}'")
PY
)"

echo "Type exactly: ${EXPECTED}"
read -r MAAS_SWITCH_CONFIRM
export MAAS_SWITCH_CONFIRM
python3 -m maas_deploy confirm-switch
