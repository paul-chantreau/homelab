#!/bin/bash
# Acceptance test for the provisioning VLAN. Run this on the MAAS server.
# A configured VLAN interface is not evidence that the GS724T is correct.
set -euo pipefail
set +x

ROOT="${MAAS_DEPLOY_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
STATE="${MAAS_STATE_DIR:-/var/lib/maas-provision}"
export MAAS_DEPLOY_ROOT="$ROOT"
export MAAS_STATE_DIR="$STATE"
export PYTHONPATH="${ROOT}${PYTHONPATH:+:$PYTHONPATH}"
ISOLATION_REPORT=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --isolation-report)
      ISOLATION_REPORT="$2"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 3
      ;;
  esac
done

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root so ping, arping, and tcpdump can inspect the interfaces." >&2
  exit 3
fi

python3 "${ROOT}/scripts/validate-config.py" \
  --mode install \
  --network "${ROOT}/config/network.yaml" \
  --deployment "${ROOT}/config/deployment.yaml"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

ip -j address >"${WORK}/address.json"
ip -d -j link >"${WORK}/link.json"
ip -j route >"${WORK}/route.json"

eval "$(python3 - "${WORK}/link.json" <<'PY'
import json
import os
import sys
from pathlib import Path
from maas_deploy.config import load_config

root = Path(os.environ["MAAS_DEPLOY_ROOT"])
config = load_config(root / "config/network.yaml", root / "config/deployment.yaml")
network = config.network
mac = str(network["management"]["interface_mac"]).lower()
links = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
physical = next(
    (item.get("ifname") for item in links if str(item.get("address", "")).lower() == mac and (item.get("linkinfo") or {}).get("info_kind") != "vlan"),
    "",
)
test_host = (network["provisioning"].get("acceptance") or {}).get("test_host") or "-"
print(f"PHYS={physical}")
print(f"GW={network['management']['gateway']}")
print(f"CONTROLLER={network['provisioning']['controller_address']}")
print(f"TEST_HOST={test_host}")
print(f"VLAN_ID={network['provisioning']['vlan_id']}")
PY
)"

if [[ -z "${PHYS}" ]]; then
  echo "no" >"${WORK}/gateway.ping"
  echo "" >"${WORK}/gateway.route"
else
  if ping -c 3 -W 2 -I "${PHYS}" "${GW}"; then
    echo yes >"${WORK}/gateway.ping"
  else
    echo no >"${WORK}/gateway.ping"
  fi
  ip route get "${GW}" >"${WORK}/gateway.route" || true
fi

echo "${TEST_HOST}" >"${WORK}/test.address"
echo no >"${WORK}/test.ping"
echo no >"${WORK}/test.arping"
: >"${WORK}/test.reverse"
if [[ "${TEST_HOST}" != "-" ]]; then
  echo "On the temporary test host, run:"
  echo "  sudo ${ROOT}/scripts/provisioning-test-host.sh --controller ${CONTROLLER}"
  python3 -m maas_deploy.listen --address "${CONTROLLER}" --port 19999 --seconds 45 --output "${WORK}/test.reverse" &
  listener=$!
  if ping -c 3 -W 2 -I provisioning "${TEST_HOST}"; then
    echo yes >"${WORK}/test.ping"
  fi
  if arping -c 3 -I provisioning "${TEST_HOST}"; then
    echo yes >"${WORK}/test.arping"
  fi
  wait "${listener}" || true
fi

if timeout 15 tcpdump -n -i provisioning -c 20 'udp port 67 or udp port 68' >"${WORK}/dhcp.txt" 2>"${WORK}/dhcp.err"; then
  echo yes >"${WORK}/dhcp.ran"
else
  status=$?
  if [[ "${status}" -eq 124 ]]; then
    echo yes >"${WORK}/dhcp.ran"
  else
    echo no >"${WORK}/dhcp.ran"
    : >"${WORK}/dhcp.txt"
  fi
fi

if maas status >"${WORK}/maas-status.txt" 2>&1; then
  true
else
  true
fi

if [[ -n "${ISOLATION_REPORT}" ]]; then
  cp "${ISOLATION_REPORT}" "${WORK}/isolation.json"
fi

python3 -m maas_deploy assemble-facts "${WORK}"
python3 -m maas_deploy write-validation "${WORK}/facts.json"
