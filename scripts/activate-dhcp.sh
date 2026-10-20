#!/bin/bash
# Enable MAAS DHCP on the provisioning VLAN only.
# The existing router DHCP configuration is not modified.
set -euo pipefail
set +x

SCRIPT_PATH="$(readlink -f -- "${BASH_SOURCE[0]}")"
ROOT="${MAAS_DEPLOY_ROOT:-$(cd "$(dirname "${SCRIPT_PATH}")/.." && pwd)}"
STATE="${MAAS_STATE_DIR:-/var/lib/maas-provision}"
export MAAS_DEPLOY_ROOT="$ROOT"
export MAAS_STATE_DIR="$STATE"
export PYTHONPATH="${ROOT}${PYTHONPATH:+:$PYTHONPATH}"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root: sudo maas-network-activate" >&2
  exit 1
fi

"${ROOT}/scripts/validate-provisioning-network.sh" "$@"

eval "$(python3 - <<'PY'
from maas_deploy.config import load_config
import os
from pathlib import Path
root = Path(os.environ["MAAS_DEPLOY_ROOT"])
config = load_config(root / "config/network.yaml", root / "config/deployment.yaml")
network = config.network
print(f"VLAN_ID={network['provisioning']['vlan_id']}")
print(f"SUBNET={network['provisioning']['subnet']}")
print(f"START={network['provisioning']['dhcp']['range_start']}")
print(f"END={network['provisioning']['dhcp']['range_end']}")
PY
)"

cat <<EOF
MAAS DHCP will be enabled only on VLAN ${VLAN_ID}.
Subnet: ${SUBNET}
Dynamic range: ${START} - ${END}
The management VLAN and the existing router DHCP service are not changed.
Type the following line to continue:
ACTIVATE DHCP ON VLAN ${VLAN_ID}
EOF
read -r MAAS_DHCP_CONFIRM
export MAAS_DHCP_CONFIRM
python3 -m maas_deploy dhcp-enable
