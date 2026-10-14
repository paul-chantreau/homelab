#!/bin/bash
# Run on a temporary host connected to a GS724T provisioning port.
# The host must already have a static address in the provisioning subnet.
set -euo pipefail
set +x

CONTROLLER=""
PORT="19999"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --controller) CONTROLLER="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done

if [[ -z "${CONTROLLER}" ]]; then
  echo "Usage: provisioning-test-host.sh --controller CONTROLLER-IP" >&2
  exit 2
fi

echo "Pinging ${CONTROLLER} from this provisioning-port host"
ping -c 3 -W 2 "${CONTROLLER}"
python3 - "${CONTROLLER}" "${PORT}" <<'PY'
import socket
import sys
import uuid

controller, port = sys.argv[1], int(sys.argv[2])
message = f"PROVISIONING-TEST-OK {uuid.getnode():012x}\n".encode()
with socket.create_connection((controller, port), timeout=10) as connection:
    connection.sendall(message)
print(f"Sent reverse connectivity probe to {controller}:{port}")
PY
