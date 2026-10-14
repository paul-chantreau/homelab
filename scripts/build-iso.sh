#!/bin/bash
# Build the customized Ubuntu Server autoinstall ISO.
set -euo pipefail
set +x

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export MAAS_DEPLOY_ROOT="$ROOT"
export PYTHONPATH="${ROOT}${PYTHONPATH:+:$PYTHONPATH}"
WORK="${ROOT}/build"
mkdir -p "${WORK}"

UBUNTU_ISO="${UBUNTU_ISO:-${WORK}/cache/ubuntu-24.04.5-live-server-amd64.iso}"
SHA256SUMS="${SHA256SUMS:-${WORK}/cache/SHA256SUMS}"
OUTPUT_ISO="${OUTPUT_ISO:-${WORK}/ubuntu-maas-autoinstall.iso}"
export UBUNTU_ISO SHA256SUMS OUTPUT_ISO

mkdir -p "$(dirname "${UBUNTU_ISO}")"
BASE_URL="$(python3 - <<'PY'
from maas_deploy.config import load_config
import os
from pathlib import Path
root = Path(os.environ["MAAS_DEPLOY_ROOT"])
ubuntu = load_config(root / "config/network.yaml", root / "config/deployment.yaml").deployment["ubuntu"]
print(ubuntu["base_url"])
PY
)"

if [[ ! -f "${SHA256SUMS}" ]]; then
  curl -fsSL "${BASE_URL}SHA256SUMS" -o "${SHA256SUMS}"
fi
if [[ ! -f "${UBUNTU_ISO}" ]]; then
  curl -fL --retry 3 "${BASE_URL}$(basename "${UBUNTU_ISO}")" -o "${UBUNTU_ISO}"
fi

python3 -m maas_deploy build-iso
echo "ISO: ${OUTPUT_ISO}"
echo "Checksum file: $(dirname "${OUTPUT_ISO}")/SHA256SUMS"
echo "Manifest: $(dirname "${OUTPUT_ISO}")/build-manifest.json"
