#!/bin/bash
# Run from a checkout on a separate wired management-LAN observer.
set -euo pipefail
set +x
SCRIPT_PATH="$(readlink -f -- "${BASH_SOURCE[0]}")"
ROOT="${MAAS_DEPLOY_ROOT:-$(cd "$(dirname "${SCRIPT_PATH}")/.." && pwd)}"
export PYTHONPATH="${ROOT}${PYTHONPATH:+:$PYTHONPATH}"
python3 -m maas_deploy.capture observe "$@"
