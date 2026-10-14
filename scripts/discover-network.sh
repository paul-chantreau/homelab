#!/bin/bash
# Read-only collection of local IP configuration.
# This does not query the Netgear switch and cannot discover VLAN membership.
set -euo pipefail
set +x

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PYTHONPATH="${ROOT}${PYTHONPATH:+:$PYTHONPATH}"
OUTPUT="${1:-${PWD}/network-discovery.json}"
SUMMARY="${OUTPUT%.json}.txt"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

ip -j address >"${WORK}/address.json"
ip -j link >"${WORK}/link.json"
ip -j route >"${WORK}/route.json"
networkctl status >"${WORK}/networkctl.txt" 2>/dev/null || true
resolvectl status >"${WORK}/resolvectl.txt" 2>/dev/null || true

python3 - "${WORK}" "${OUTPUT}" "${SUMMARY}" <<'PY'
import json
import sys
from pathlib import Path

from maas_deploy.discovery import assemble, summary

work = Path(sys.argv[1])
report = assemble(
    json.loads((work / "address.json").read_text(encoding="utf-8")),
    json.loads((work / "link.json").read_text(encoding="utf-8")),
    json.loads((work / "route.json").read_text(encoding="utf-8")),
    (work / "networkctl.txt").read_text(encoding="utf-8"),
    (work / "resolvectl.txt").read_text(encoding="utf-8"),
)
Path(sys.argv[2]).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
text = summary(report)
Path(sys.argv[3]).write_text(text, encoding="utf-8")
print(text)
print(f"Wrote {sys.argv[2]}")
PY
