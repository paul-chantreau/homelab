#!/bin/bash
# Stop MAAS DHCP on the provisioning VLAN. Router DHCP is left unchanged.
set -euo pipefail
set +x

ROOT="${MAAS_DEPLOY_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
export MAAS_DEPLOY_ROOT="$ROOT"
export MAAS_STATE_DIR="${MAAS_STATE_DIR:-/var/lib/maas-provision}"
export PYTHONPATH="${ROOT}${PYTHONPATH:+:$PYTHONPATH}"
EMERGENCY="0"

if [[ "${1:-}" == "--emergency" ]]; then
  EMERGENCY="1"
fi

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root: sudo maas-network-deactivate" >&2
  exit 1
fi

eval "$(python3 - <<'PY'
from maas_deploy.config import load_config
import os
from pathlib import Path
root = Path(os.environ["MAAS_DEPLOY_ROOT"])
config = load_config(root / "config/network.yaml", root / "config/deployment.yaml")
print(f"VLAN_ID={config.network['provisioning']['vlan_id']}")
PY
)"

if [[ "${EMERGENCY}" == "1" ]]; then
  PROMPT="EMERGENCY DEACTIVATE DHCP ON VLAN ${VLAN_ID}"
else
  PROMPT="DEACTIVATE DHCP ON VLAN ${VLAN_ID}"
fi
echo "Type the following line to stop MAAS DHCP on VLAN ${VLAN_ID}:"
echo "${PROMPT}"
read -r MAAS_DHCP_CONFIRM
export MAAS_DHCP_CONFIRM
if [[ "${EMERGENCY}" == "1" ]]; then
  export MAAS_DHCP_EMERGENCY=1
fi
python3 -m maas_deploy dhcp-disable
