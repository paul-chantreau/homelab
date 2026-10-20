#!/bin/bash
set -euo pipefail
set +x
if [[ "${1:-}" != "--confirm" || "${2:-}" != "RESTORE" || -z "${3:-}" ]]; then
  echo "Usage: sudo maas-restore --confirm RESTORE /path/to/maas.dump" >&2
  exit 2
fi
SCRIPT_PATH="$(readlink -f -- "${BASH_SOURCE[0]}")"
ROOT="${MAAS_DEPLOY_ROOT:-$(cd "$(dirname "${SCRIPT_PATH}")/.." && pwd)}"
export MAAS_DEPLOY_ROOT="$ROOT"
export PYTHONPATH="${ROOT}${PYTHONPATH:+:$PYTHONPATH}"
python3 -m maas_deploy restore "$3"
