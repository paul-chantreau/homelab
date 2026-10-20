#!/bin/bash
# Query the actual MAAS VLAN and service state, including after DHCP activation.
set -euo pipefail
set +x
SCRIPT_PATH="$(readlink -f -- "${BASH_SOURCE[0]}")"
ROOT="${MAAS_DEPLOY_ROOT:-$(cd "$(dirname "${SCRIPT_PATH}")/.." && pwd)}"
export MAAS_DEPLOY_ROOT="$ROOT"
export PYTHONPATH="${ROOT}${PYTHONPATH:+:$PYTHONPATH}"
python3 -m maas_deploy healthcheck
