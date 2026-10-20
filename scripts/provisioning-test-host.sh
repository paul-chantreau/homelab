#!/bin/bash
# Run as root from the temporary provisioning host, with the printed one-use token.
set -euo pipefail
set +x
CONTROLLER=""
TOKEN=""
PORT=19999
while [[ $# -gt 0 ]]; do
  case "$1" in
    --controller) CONTROLLER="$2"; shift 2 ;;
    --token) TOKEN="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done
if [[ -z "$CONTROLLER" || -z "$TOKEN" ]]; then
  echo "Usage: sudo provisioning-test-host.sh --controller IP --token TOKEN" >&2
  exit 2
fi
python3 - "$CONTROLLER" "$PORT" "$TOKEN" <<'PY'
from datetime import datetime, timezone
import json
import socket
import subprocess
import sys
controller, port, token = sys.argv[1:]
route = json.loads(subprocess.check_output(['ip', '-j', 'route', 'get', controller], text=True))[0]
subprocess.run(['ping', '-c', '3', '-W', '2', '-I', route['dev'], controller], check=True)
with socket.create_connection((controller, int(port)), timeout=10) as connection:
    source = connection.getsockname()[0]
    started = datetime.now(timezone.utc)
    # Unsolicited ARP generates deterministic L2 broadcast, even with warm ARP caches.
    subprocess.run(['arping', '-U', '-c', '3', '-w', '4', '-I', route['dev'], source], check=True)
    probe = {'token': token, 'broadcast_sent': True,
             'broadcast_started_at': started.isoformat(),
             'broadcast_finished_at': datetime.now(timezone.utc).isoformat()}
    connection.sendall((json.dumps(probe) + '\n').encode())
print('Broadcast probe sent and correlated with the controller session.')
PY
