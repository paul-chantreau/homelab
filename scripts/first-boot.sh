#!/bin/bash
# Local, restartable Ansible provisioning. A failure leaves the completion marker absent.
set -euo pipefail
set +x

ROOT="${MAAS_DEPLOY_ROOT:-/opt/maas-deploy}"
STATE="${MAAS_STATE_DIR:-/var/lib/maas-provision}"
LOG_DIR="${MAAS_LOG_DIR:-/var/log/maas-provision}"
mkdir -p "${STATE}" "${LOG_DIR}"
exec >>"${LOG_DIR}/first-boot.log" 2>&1

if [[ -f "${STATE}/complete" ]]; then
  echo "Provisioning is already complete."
  exit 0
fi

export MAAS_DEPLOY_ROOT="$ROOT"
export MAAS_STATE_DIR="$STATE"
export PYTHONPATH="${ROOT}${PYTHONPATH:+:$PYTHONPATH}"
export ANSIBLE_CONFIG="${ROOT}/ansible/ansible.cfg"
export ANSIBLE_LOG_PATH="${LOG_DIR}/ansible.log"

cd "${ROOT}/ansible"
if ! ansible-playbook install.yml; then
  echo "Provisioning failed. Fix the cause and start maas-firstboot.service again."
  exit 1
fi
touch "${STATE}/complete"
echo "Provisioning completed."
if ! python3 -m maas_deploy console-access; then
  echo "MAAS is ready, but its access details could not be displayed on tty1."
fi
