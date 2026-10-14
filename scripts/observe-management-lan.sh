#!/bin/bash
# Capture on a second host that is on the management LAN.
# Do not run this on the MAAS server. Its trunk port is supposed to carry VLAN 100.
set -euo pipefail
set +x

INTERFACE=""
VLAN=""
HOST_ADDRESS=""
SECONDS_TO_CAPTURE="30"
OUTPUT="isolation-report.json"
MAAS_MAC=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --interface) INTERFACE="$2"; shift 2 ;;
    --vlan) VLAN="$2"; shift 2 ;;
    --host) HOST_ADDRESS="$2"; shift 2 ;;
    --seconds) SECONDS_TO_CAPTURE="$2"; shift 2 ;;
    --output) OUTPUT="$2"; shift 2 ;;
    --maas-mac) MAAS_MAC="$2"; shift 2 ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done

if [[ -z "${INTERFACE}" || -z "${VLAN}" || -z "${HOST_ADDRESS}" ]]; then
  echo "Usage: observe-management-lan.sh --interface IFACE --vlan 100 --host TEST-IP [--maas-mac MAC]" >&2
  exit 2
fi

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root to capture packets." >&2
  exit 2
fi

local_macs="$(ip -j link | python3 -c 'import json,sys; print(" ".join(str(i.get("address","")).lower() for i in json.load(sys.stdin)))')"
if [[ -n "${MAAS_MAC}" && " ${local_macs} " == *" ${MAAS_MAC,,} "* ]]; then
  echo "This host has the MAAS server MAC. A capture here cannot prove VLAN isolation." >&2
  exit 1
fi

capture="$(mktemp)"
trap 'rm -f "$capture"' EXIT
if timeout "${SECONDS_TO_CAPTURE}" tcpdump -n -i "${INTERFACE}" -c 50 "vlan ${VLAN} or host ${HOST_ADDRESS}" >"${capture}" 2>"${capture}.err"; then
  true
fi
python3 - "${capture}" "${OUTPUT}" "${INTERFACE}" "${VLAN}" "${HOST_ADDRESS}" "${SECONDS_TO_CAPTURE}" <<'PY'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

capture, output, interface, vlan, host, seconds = sys.argv[1:]
text = Path(capture).read_text(encoding="utf-8", errors="replace")
packets = [line for line in text.splitlines() if line and not line.startswith("tcpdump:")]
document = {
    "observer": "management-lan",
    "captured_on_maas_server": False,
    "interface": interface,
    "vlan_id": int(vlan),
    "observed_host": host,
    "seconds": int(seconds),
    "packet_count": len(packets),
    "started_at": datetime.now(timezone.utc).isoformat(),
}
Path(output).write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
print(f"Wrote {output} with packet_count={len(packets)}")
PY
