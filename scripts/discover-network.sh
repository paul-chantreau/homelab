#!/bin/bash
# Read-only, console-only collection of local IP configuration.
set -euo pipefail
set +x

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PYTHONPATH="${ROOT}${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -B -m maas_deploy.discovery "$@"
