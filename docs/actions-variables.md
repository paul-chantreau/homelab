# GitHub Actions installation variables
Create repository **Variables**, not Secrets, in Settings → Secrets and variables → Actions → Variables.
Only public SSH keys belong in `SSH_PUBLIC_KEY`. MAAS/PostgreSQL passwords and the MAAS API key remain generated on the installed server. Never upload a private SSH key.

## Create missing variables

From an authorized workstation with Python 3 and GitHub CLI installed and authenticated:

```bash
python3 scripts/create-actions-variables.py --repo paul-chantreau/homelab
```

The GitHub identity must have repository Actions **Variables read/write** permission. The script reads all existing variables first, preserves every existing value, and creates only missing entries. Required fields and disk alternatives start as `__REQUIRED__`; replace these placeholders with actual site values. Fill at least one disk identifier; leave the other disk placeholders alone or delete those unused variables. Optional variables are created with the defaults below. If access is denied, nothing is created. If creation fails partway through, earlier additions remain; rerunning safely resumes without overwriting them.

## Required: no defaults

| Variable | Format |
| --- | --- |
| `SSH_PUBLIC_KEY` | keys |
| `SWITCH_FIRMWARE_VERSION` | string |
| `SWITCH_ROUTER_PORT` | integer |
| `SWITCH_MAAS_PORT` | integer |
| `SWITCH_PROVISIONING_PORTS` | integer-list |
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

`SSH_PUBLIC_KEY` accepts one public key per line. Lists must be JSON, e.g. `[5,6,7]` for switch ports or `["192.0.2.1"]` for DNS. Use real values from the site, not these documentation examples. Both VLAN IDs must be explicitly set; the YAML placeholders are never used as Actions defaults.

## Disk: at least one identifier

| Variable | Format |
| --- | --- |
| `DISK_SERIAL` | string |
| `DISK_WWN` | string |
| `DISK_BY_ID` | string |

Identify the disk on the target server using `lsblk -d -o NAME,MODEL,SERIAL,WWN,SIZE,TYPE` and `ls -l /dev/disk/by-id/`. Multiple identifiers must match the same disk. `DISK_BY_ID` must be a `/dev/disk/by-id/...` path.

## Optional: defaults applied when missing or blank

| Variable | Default |
| --- | --- |
| `HOSTNAME` | `maas-controller` |
| `TIMEZONE` | `Europe/Paris` |
| `LOCALE` | `en_US.UTF-8` |
| `KEYBOARD_LAYOUT` | `fr` |
| `KEYBOARD_VARIANT` | `legacy` |
| `ADMIN_USER` | `maasadmin` |
| `MAAS_ADMIN_USERNAME` | `admin` |
| `MAAS_ADMIN_EMAIL` | `admin@maas.local` |
| `POSTGRES_DATABASE` | `maas` |
| `POSTGRES_USER` | `maas` |
| `BACKUP_DIRECTORY` | `/var/backups/maas` |
| `BACKUP_RETAIN` | `14` |

## Check and build

Run **Check installation variables** in Actions to validate current repository variables without downloading an ISO or modifying the configuration. This workflow also runs on pushes to `main`. Missing repository variables do not block the independent pull-request code validation suite.

The **Build MAAS autoinstall ISO** manual workflow invokes the same local composite action, applies defaults, validates all required inputs and cross-field installation rules, then writes the effective `network.yaml` and `deployment.yaml` only into the runner checkout. These files are embedded in the ISO and copied to the installed server. Nothing is committed by the workflow. The summary lists variable names and whether values were configured or defaulted, without printing values.

There is no `confirm_disk_erase` workflow input. A manual ISO build with a valid disk identity prepares an **installation ISO that will erase that matching disk when booted**. The internal `disk.confirm_erase` flag is set automatically for this build. The installer still refuses to proceed unless exactly one physical disk matches the configured identity. DHCP activation and switch confirmation remain explicit on-site operations.

The following are not Actions inputs: `DISK_MODEL`, `PROVISIONING_GATEWAY`, `PROVISIONING_DNS`, `BACKUP_OFFHOST_TARGET`. Generated configuration fixes disk model to null, provisioning gateway to null, provisioning DNS to an empty list, and off-host backup target to null. Backups therefore stay on the controller. Existing variables with these names are ignored; the creation script does not delete existing user variables.

Changing GitHub variables affects future builds, not an already installed controller. Configuration can be read from the ISO; GitHub Variables are not confidential storage. Keep compatibility versions, checksum expectations and network safety constraints in version control.

For local checking without writing files, supply a JSON object as `ACTIONS_VARIABLES_JSON` and run `python3 scripts/configure-actions.py`. Add `--write-config` only in a disposable build checkout: it replaces the canonical YAML with the validated configuration.
