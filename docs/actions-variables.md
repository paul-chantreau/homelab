# GitHub Actions configuration: Secrets and Variables

## Migrate existing topology Variables

From an authenticated GitHub CLI on your workstation:

```powershell
py .\scripts\migrate-actions-secrets.py
```

The identity needs repository Actions Variables and Secrets read/write permissions. Values are sent to `gh secret set` through standard input, never command-line arguments, temporary files or logs. The script creates each missing Secret, confirms its metadata, then deletes its source Variable. Existing Secrets are never overwritten. If both exist, it preserves both and reports the duplicate for manual review; remove the public Variable after verifying your Secret configuration. A partial failure leaves unmigrated values untouched and the script can safely be rerun.

This migration cannot remove information already present in historical logs, downloads or old artifacts. Review their access and delete obsolete artifacts where necessary.

## Required Actions Secrets

Open Settings → Secrets and variables → Actions → **Secrets** → New repository secret. Keep the following names exactly as listed. No fallback to public Variables is allowed.

| Secret | Format |
| --- | --- |
| `MANAGEMENT_INTERFACE_MAC` | string |
| `MANAGEMENT_ADDRESS` | string |
| `MANAGEMENT_GATEWAY` | string |
| `MANAGEMENT_DNS` | string-list |
| `MANAGEMENT_VLAN_ID` | integer |
| `PROVISIONING_VLAN_ID` | integer |
| `PROVISIONING_SUBNET` | string |
| `PROVISIONING_CONTROLLER_ADDRESS` | string |
| `PROVISIONING_DHCP_START` | string |
| `PROVISIONING_DHCP_END` | string |
| `PROVISIONING_TEST_HOST` | string |

These 11 Secrets correspond to every field in `config/network.yaml`. DNS lists use JSON, for example `["192.0.2.1"]`. Use your actual site values. VLAN IDs must be explicitly supplied. `PROVISIONING_TEST_HOST` is outside the DHCP pool and different from the controller.

The GS724Tv4 model, isolated VLAN topology, disabled forwarding, cleared provisioning gateway/DNS options, and explicit DHCP activation are fixed behavior. Physical port numbers are optional private notes in the [wiring worksheet](../README.md#3-physical-port-mapping). They are not build inputs. Existing `SWITCH_ROUTER_PORT`, `SWITCH_MAAS_PORT`, and `SWITCH_PROVISIONING_PORTS` Secrets or Variables can be deleted; workflows ignore them. Physical switch inspection and acceptance remain mandatory.

## Disk Secrets: at least one required

| Secret | Format |
| --- | --- |
| `DISK_SERIAL` | string |
| `DISK_WWN` | string |
| `DISK_BY_ID` | string |

Identify the disk on the target server with `lsblk -d -o NAME,MODEL,SERIAL,WWN,SIZE,TYPE` and `ls -l /dev/disk/by-id/`. Multiple identifiers must identify the same disk. `DISK_BY_ID` must start with `/dev/disk/by-id/`. Unused alternatives may be absent; do not create placeholder Secrets.

## Required Actions Variable

`SSH_PUBLIC_KEY` remains a **Variable**. Supply public `.pub` contents, one public key per line. Never upload a private SSH key. The account's private SSH key stays on your workstation.

## Optional Actions Variables with defaults

| Variable | Default |
| --- | --- |
| `HOSTNAME` | `maas-controller` |
| `TIMEZONE` | `Europe/Paris` |
| `LOCALE` | `en_US.UTF-8` |
| `KEYBOARD_LAYOUT` | `fr` |
| `KEYBOARD_VARIANT` | `latin9` |
| `ADMIN_USER` | `maasadmin` |
| `MAAS_ADMIN_USERNAME` | `admin` |
| `MAAS_ADMIN_EMAIL` | `admin@maas.local` |
| `POSTGRES_DATABASE` | `maas` |
| `POSTGRES_USER` | `maas` |
| `BACKUP_DIRECTORY` | `/var/backups/maas` |
| `BACKUP_RETAIN` | `14` |

Create missing public Variables without overwriting existing values:

```powershell
py .\scripts\create-actions-variables.py
```

The script creates only Variables, never topology Secrets. Configure missing Secrets manually or use the migration helper above.

## Check and build

**Check installation variables** runs manually and on pushes to `main`. The manual ISO workflow invokes the same local composite action. Both explicitly pass only the allowlisted topology/disk Secrets, never `toJSON(secrets)`. Pull-request validation never needs the installation Secrets.

The Python checker resolves Secrets and Variables separately, applies optional defaults, and validates installation constraints before ISO download. Its summary and errors show field names/statuses, never values. Secret fields accidentally left in Variables are ignored. Missing Secrets fail the check, even if an equivalent Variable exists.

For an ISO build, validated YAML files replace the runner's checkout copies with mode 0600 and are embedded in the ISO for the installed server. An always-run cleanup step removes those YAML files and the generated ISO from the runner after artifact handling. This is ordinary cleanup, not a guarantee of secure erasure.

**The ISO still contains the full resolved network topology and disk identity.** GitHub Secrets protect configuration storage and help mask logs; they do not encrypt an ISO or protect it from someone who can download it. Control repository/artifact access and protect downloaded copies. The MAAS/PostgreSQL passwords and API key remain generated on the installed server and are not supplied by GitHub.

There is no `confirm_disk_erase` workflow input: manually building with a valid disk identity prepares an installation ISO that **erases that matching disk when booted**. The internal flag is set automatically. The physical installer still requires exactly one matching disk; switch confirmation and DHCP activation remain on-site steps.

`DISK_MODEL`, `PROVISIONING_GATEWAY`, `PROVISIONING_DNS` and `BACKUP_OFFHOST_TARGET` are not Actions inputs. The disk model and off-host backup target remain null; provisioning gateway/DNS options are absent from YAML and explicitly cleared in MAAS. Backups stay on the controller. Changing GitHub settings affects future builds, not an installed server. For older local YAML files, retain only the 11 network inputs listed above and revalidate before installation or a playbook rerun. Obsolete fields are rejected; the changed configuration hash requires fresh switch confirmation and acceptance.

For local checks, supply public settings as `ACTIONS_VARIABLES_JSON` and confidential settings as `ACTIONS_SECRETS_JSON`, then run `python3 scripts/configure-actions.py`. Do not print or paste the confidential JSON. Use `--write-config` only in a disposable build checkout.
