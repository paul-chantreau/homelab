# Corrections following the installation review

This change fixes the code defects identified in the review of commit 62a4ca1.

| Review | Correction |
| --- | --- |
| R01 disk identity | Every rendered ISO starts with an impossible selector. The early-command checks exactly one unmounted disk by serial/WWN/by-id, excludes mounted installation media, and replaces the guard with that device's exact runtime path. No unsupported WWN or by-id directive reaches Subiquity. |
| R02 bootstrap | No MAAS executable is invoked before snap installation. Existing mode is read from the local parsable configuration. |
| R03 URL | Initial URL is supplied to `maas init`; an existing controller uses `maas config --maas-url` only when needed. No invalid `set-config name=maas_url` API call remains. |
| R04 services | A shared parser reads Pebble's Current column and recognizes the legacy format. Readiness requires region/rack service state plus a functioning version API response. |
| R05 managed subnet | Provisioning is managed, while DHCP remains disabled until explicit acceptance/activation. |
| R06 fabric/VLAN | The VLAN is anchored to the sole rack's physical MAC and provisioning interface, including parent-interface name. Subnet creation supplies its VLAN ID. Activation refuses inconsistent VLANs, unmanaged subnets or missing dynamic ranges. |
| R07 installed commands | Helpers resolve their own symlink before finding the checkout. The installed tree is owned by root. |
| R08 capture | Packet capture must complete for its measured interval. DHCP replies are decoded from pcap bytes. Isolation acceptance needs a fresh broadcast probe from the expected host, correlated with a capture on a verified management interface. |
| R09 backup/restore | Root opens private files and passes descriptors to PostgreSQL. Restore prepares a temporary database, verifies MAAS tables and migration compatibility, then swaps names. The old database remains available; failed service readiness rolls back the names. |
| R10 images | Architecture, resource sets and file completeness are checked. A marker no longer bypasses API verification. |
| R11 health | Health reads actual MAAS VLAN DHCP configuration instead of guessing it from the dhcpd process. |
| R12 inputs | Public keys are checked by ssh-keygen, timezone by zoneinfo, and keyboard/locale against metadata extracted from the checksum-verified Ubuntu 24.04.5 source ISO. |
| R13 credentials | A locked, fsynced temporary file is atomically replaced; interrupted replacement cannot truncate existing passwords. |

The account is explicitly configured for key-only login and passwordless sudo, because it is an administrative account whose password remains locked. Protect its private SSH key accordingly.

An additional full-install check exposed Subiquity's default offline fallback: if mirror selection fails, the installer can partition/extract Ubuntu and later fail because Ansible is not on the installation medium. The template now uses the official HTTPS archive/security endpoints and `apt.fallback: abort`, since installing this controller requires online packages and snap downloads. The VM runner records the live installer journal and stops promptly on installer or first-boot failure.

## Revised physical acceptance procedure

A successful capture is evidence about the specific broadcast probe, not a general firewall or all possible network paths. Review the switch membership/PVID and retain the physical isolation checks. Use a separate **wired** management observer. A passive Wi-Fi capture is insufficient.

The observer needs a checkout (or copies of the scripts and `maas_deploy` package), Python 3, `ip` and `tcpdump`. The provisioning test host needs Python 3, `ip`, `ping`, and `iputils-arping`, a static test address, and no default route.

1. Check the current switch configuration and run `sudo maas-network-confirm-switch` on the controller.
2. Remove any old output report. On the observer start a 60-second capture:

```bash
sudo scripts/observe-management-lan.sh \
  --interface <observer-interface> \
  --management-subnet <management-CIDR> \
  --vlan 100 --host <test-host-IP> \
  --maas-mac <controller-MAC> \
  --seconds 60 --output isolation-report.json
```

3. Immediately run on the controller:

```bash
sudo maas-network-activate --isolation-report /tmp/isolation-report.json
```

4. Run the exact command printed by the controller on the provisioning test host, including its newly generated `--token`. The helper sends a deterministic ARP broadcast and returns timestamped evidence over a connection from the expected source address.
5. When the observer capture finishes, copy `isolation-report.json` to the controller's requested path. The controller waits up to 120 seconds for it. The capture must cover the broadcast probe and be less than five minutes old. A failed capture, wrong interface, stale report or missing probe gives INCONCLUSIVE and blocks activation.
6. Only after all acceptance checks pass, type the explicit DHCP activation phrase. For validation without activation, use `maas-network-validate` instead; a later activation requires a new complete capture/probe session.

Never interpret zero packets by itself as successful isolation. Initial DHCP clients have no assigned IP; future firewall rules must account for broadcasts and TFTP negotiated flows. No firewall is enabled by this corrective change.

Synchronize the controller, observer and temporary host clocks before starting; correlation compares UTC timestamps from these machines.

## Backups and restoration

`sudo maas-backup` creates a private `.dump` plus a companion `.config.tar.gz` containing the effective YAML and credential file. Both are mode 0600. Keep a protected off-machine copy; the current Actions configuration intentionally does not enable automatic off-host export.

The database restore command remains:

```bash
sudo maas-restore --confirm RESTORE /path/to/maas.dump
```

It requires an already initialized, compatible MAAS/PostgreSQL installation and the corresponding credential/configuration recovery. It first restores into a separate database while the current MAAS instance is still running, verifies expected tables and identical migration names, then stops MAAS and swaps the databases. A failed availability check reselects the old database. On success the previous database is retained as `..._before_...`; manage those rollback copies explicitly after confirming recovery. Allow sufficient disk capacity for both databases.

This is database recovery, not a full hardware disaster-recovery image. The bundle does not replace a tested snap snapshot/configuration recovery plan. The ISO still contains topology in clear text; control its access.

## Validation levels

Run unit tests with `python3 -m pytest -q`. PostgreSQL integration tests are explicitly enabled with:

```bash
HOMELAB_TEST_POSTGRES=1 python3 -m pytest -q tests/integration/test_postgresql_restore.py
```

They start and remove an isolated PostgreSQL 16 Docker container, with no host ports and no host mounts. They exercise real dump/restore, invalid archive rejection, schema mismatch and rollback on readiness failure. Only MAAS stop/start is simulated in this database test.

The manual **Full ISO installation smoke test** workflow boots the actual customized Ubuntu ISO in an isolated UEFI QEMU guest with synthetic configuration and an ephemeral key. It checks the completed MAAS provisioning, installed helper symlinks, backup/database restore, a second Ansible run and recovery after reboot. It never uses installation Secrets and uploads only synthetic-test diagnostics, not the ISO or disk. It needs at least 18 GiB free; KVM is preferred, with TCG fallback.

A completed VM test does not prove physical GS724Tv4 isolation, PXE commissioning/deployment of actual nodes, or recovery onto replacement physical hardware. Report each level separately. Do not infer end-to-end readiness from the unit-test count.

## Official references used

- [Subiquity autoinstall reference, including early-command reload](https://canonical-subiquity.readthedocs-hosted.com/en/latest/reference/autoinstall-reference.html)
- [Subiquity 24.04.5 disk matching implementation](https://github.com/canonical/subiquity/blob/24.04.5/subiquity/models/filesystem.py)
- [MAAS installation guidance](https://canonical.com/maas/docs/stable/how-to-guides/get-started/install-maas/)
- [MAAS 3.7 CLI and API implementation](https://github.com/canonical/maas/tree/3.7)
- [MAAS network management](https://canonical.com/maas/docs/stable/how-to-guides/manage-networks/)
- [Pebble service status commands](https://documentation.ubuntu.com/pebble/reference/cli-commands/)

Consulted on 2026-10-20. The generated input metadata also records the pinned Ubuntu ISO checksum and source package versions.
