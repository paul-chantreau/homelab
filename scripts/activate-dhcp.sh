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

if [[ "$#" -ne 0 ]]; then
  echo "Usage: sudo maas-network-activate" >&2
  exit 1
fi

DHCP_SETTINGS="$(python3 - <<'PY'
from maas_deploy.config import INSTALL_MODE, load_config, validate
import os
from pathlib import Path
root = Path(os.environ["MAAS_DEPLOY_ROOT"])
config = load_config(root / "config/network.yaml", root / "config/deployment.yaml")
validate(config, INSTALL_MODE)
network = config.network
print(network['provisioning']['vlan_id'], network['provisioning']['subnet'],
      network['provisioning']['dhcp']['range_start'], network['provisioning']['dhcp']['range_end'])
PY
)"
read -r VLAN_ID SUBNET START END <<< "${DHCP_SETTINGS}"

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
