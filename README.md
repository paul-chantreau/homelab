# Production MAAS on one Ubuntu server and a Netgear GS724Tv4

This repository builds an Ubuntu Server autoinstall ISO along with Ansible to install MAAS. It assumes the switch is a Netgear GS724Tv4 ; the configuration and procedures use that specific model.

## 1. Requirements

One dedicated bare-metal server:

- Exactly one physical Ethernet interface. Netplan selects it by MAC address.
- Enough disk space for Ubuntu, PostgreSQL, and MAAS images.
- A disk you can identify by serial, WWN, or a `/dev/disk/by-id/` path.
- UEFI or legacy BIOS boot from USB.
- Out-of-band or local console access for setup and recovery. The installed account password is locked; normal administrative login uses an SSH public key.

You will also need an out-of-band or local console access to reach the Netgear GS724Tv4 management web interface on the existing LAN.

## 2. Network discovery and management settings

Run discovery on a computer connected to the existing management LAN.

On Windows PowerShell, from the repository directory:

```powershell
.\scripts\discover-network.ps1
```

This writes `network-discovery.json`. To check the workstation's IPv4 prefix as well:

```powershell
Get-NetIPAddress -AddressFamily IPv4 | Select-Object InterfaceAlias,IPAddress,PrefixLength
```

On Linux:

```bash
scripts/discover-network.sh
```

This writes `network-discovery.json` and `network-discovery.txt` using `ip`, `networkctl`, and `resolvectl`. Keep discovery reports in private local notes.

Check the router's current LAN subnet, DHCP scope, gateway, and DNS settings. Find the switch management IP in the router's DHCP lease table by matching the MAC on the switch label. In the switch UI, read the existing management VLAN and port PVIDs.

Then create these **repository Actions Secrets**, using the Settings procedure in [section 5](#5-github-actions-secrets-and-variables):

| Secret | What to enter and where to find it |
| --- | --- |
| `MANAGEMENT_INTERFACE_MAC` | The MAAS server's physical Ethernet MAC from its firmware, NIC label, or `ip link` on that server. Do not use the workstation's Wi-Fi MAC or the switch MAC. |
| `MANAGEMENT_ADDRESS` | Choose an unused fixed IPv4 address for the MAAS server on the existing LAN, including its prefix, such as `192.0.2.10/24`. Keep it outside the dynamic DHCP pool or explicitly reserved for this server. |
| `MANAGEMENT_GATEWAY` | The existing router's IPv4 address on that LAN, confirmed in the router UI. |
| `MANAGEMENT_DNS` | Reachable IPv4 DNS resolvers as a nonempty JSON list, such as `["192.0.2.1"]`. Confirm the values in the router settings or discovery report. |
| `MANAGEMENT_VLAN_ID` | The existing untagged management VLAN/PVID from the switch UI, as an integer. Do not change the switch management VLAN to match an example. |

The computer's own address identifies the LAN and prefix; `MANAGEMENT_ADDRESS` is the separate address chosen for the MAAS server.

Choose the provisioning network and its Secrets in [section 4](#4-vlan-configuration).

## 3. Physical port mapping

Identify and record in local notes the switch ports connected to the router, the MAAS server, and each node before changing VLAN membership.

Record the port numbers in private local notes. **Physical port numbers have no corresponding GitHub Secret or Variable.** Software does not configure or query the switch ports. Do not create `SWITCH_ROUTER_PORT`, `SWITCH_MAAS_PORT`, or `SWITCH_PROVISIONING_PORTS`; those inputs were removed.

The related Actions settings are the VLAN IDs: keep `MANAGEMENT_VLAN_ID` equal to the existing management VLAN discovered in [section 2](#2-network-discovery-and-management-settings), and choose `PROVISIONING_VLAN_ID` in [section 4](#4-vlan-configuration). Both are required **Secrets**, even when you use VLAN 1 and VLAN 100.

Optionally copy this worksheet and fill in the physical port numbers:

| Device | Switch port (local notes) | VLAN membership | PVID |
| --- | --- | --- | --- |
| Router | User-defined | Existing `MANAGEMENT_VLAN_ID`, unchanged; exclude `PROVISIONING_VLAN_ID` | Existing management PVID |
| MAAS server | User-defined | `MANAGEMENT_VLAN_ID` U + `PROVISIONING_VLAN_ID` T | `MANAGEMENT_VLAN_ID` |
| Node 1 | User-defined | `PROVISIONING_VLAN_ID` U only | `PROVISIONING_VLAN_ID` |
| Node 2 | User-defined | `PROVISIONING_VLAN_ID` U only | `PROVISIONING_VLAN_ID` |
| Additional nodes | User-defined | `PROVISIONING_VLAN_ID` U only | `PROVISIONING_VLAN_ID` |

`MANAGEMENT_INTERFACE_MAC` identifies the server NIC connected to its chosen switch port; it is not a switch-port identifier. Apply the membership/PVID choices in [section 4](#4-vlan-configuration).

After controller installation, `sudo maas-network-confirm-switch` prints the required membership and PVID by role using these VLAN Secrets. Inspect the actual connected ports in the switch UI before confirming. The worksheet itself is optional.

## 4. VLAN configuration

Choose the provisioning network, save its **repository Actions Secrets**, then configure the physical switch. Use the physical port roles from [section 3](#3-physical-port-mapping).

| Secret | Selection rule |
| --- | --- |
| `PROVISIONING_VLAN_ID` | A separate VLAN from `MANAGEMENT_VLAN_ID`, between 4 and 4093. VLAN 100 is an example, not a default. VLANs 1, 2, and 3 are predefined on the GS724Tv4 and rejected for provisioning. |
| `PROVISIONING_SUBNET` | An IPv4 network in CIDR form, such as `198.51.100.0/24`, that does not overlap the management LAN. |
| `PROVISIONING_CONTROLLER_ADDRESS` | The MAAS server's host IPv4 address inside `PROVISIONING_SUBNET`, outside the DHCP pool. Enter the IP without a prefix. |
| `PROVISIONING_DHCP_START` | First dynamic IPv4 address inside `PROVISIONING_SUBNET`. |
| `PROVISIONING_DHCP_END` | Last dynamic IPv4 address in that subnet; it must be greater than `PROVISIONING_DHCP_START`. |
| `PROVISIONING_TEST_HOST` | Static IPv4 address for the temporary wired validation host, inside the subnet, outside the DHCP pool, and different from `PROVISIONING_CONTROLLER_ADDRESS`. |

Keep the controller, test host, and DHCP endpoints away from the subnet and broadcast addresses. The provisioning VLAN stays isolated: the server does not forward packets, no provisioning gateway is configured, and MAAS DHCP starts disabled.

The instructions below use **100 to mean the value saved in `PROVISIONING_VLAN_ID`**. Substitute your chosen value throughout if it differs. The management VLAN means `MANAGEMENT_VLAN_ID`. A passing Ubuntu VLAN interface does not prove the switch pages are correct.

Membership letters:

| Letter | Meaning |
|---|---|
| T | Tagged member. Frames for this VLAN keep the VLAN tag on this port |
| U | Untagged member. Frames for this VLAN leave this port without a tag. A port is untagged in only one VLAN |
| Blank | Excluded. This port is not a member of this VLAN |

PVID is separate from membership. The PVID is the VLAN assigned to untagged frames arriving on that port. Creating VLAN 100 only adds an empty VLAN. Until membership and PVID are set, the server still sends and receives untagged management traffic, and a machine port still puts its untagged frames into the management VLAN (often VLAN 1).

Target, using the port numbers you recorded:

```text
Port connected to MAAS:
  Management VLAN: U
  Provisioning VLAN 100: T
  PVID: management VLAN ID

Port connected to a machine:
  Management VLAN: excluded
  Provisioning VLAN 100: U
  PVID: 100

Port connected to the router:
  Unchanged. Do not add VLAN 100
```

### Safe order

Do these steps in this order so a mistake does not remove the path you are using to administer the switch.

1. Save a switch configuration backup using the Maintenance procedure in the GS724Tv4 manual, and confirm the existing management VLAN ID.
2. Create VLAN 100. Do not edit port membership yet. Apply. Confirm the VLAN is in the VLAN list. Confirm the web interface still loads.
3. On the MAAS server port only, add tagged membership in VLAN 100. Leave its untagged management membership and its PVID as they are. Apply. Open the web interface again.
4. On one machine port, remove untagged membership in the management VLAN. Do not leave that port untagged in VLAN 2 or VLAN 3 either. Add untagged membership in VLAN 100. Set that port's PVID to 100. Apply. Confirm the web interface still loads from the management LAN.
5. Repeat step 4 for each remaining machine port.
6. Do not change the router port.
7. Re-open VLAN Membership and Port PVID Configuration. Compare them with the worksheet in [section 3](#3-physical-port-mapping).
8. If the GS724Tv4 manual requires a separate write of the running configuration to non-volatile memory, do that step from the manual after Apply. Re-open the pages and confirm the values are still there.

### Menus

From the 25 September 2013 administration manual:

1. Switching, VLAN, Basic, VLAN Configuration. Create VLAN 100. Apply. Do not delete VLAN 1, 2, or 3.
2. Switching, VLAN, Advanced, VLAN Membership. Machine ports must not remain untagged members of VLAN 1, 2, or 3 once they are untagged in VLAN 100, because a port is untagged in only one VLAN. The MAAS port stays untagged in the management VLAN and tagged in VLAN 100. Apply.
3. Switching, VLAN, Advanced, Port PVID Configuration. Machine ports use PVID 100. The MAAS port keeps the management PVID. Apply.

The manual documents Routing, VLAN, VLAN Routing Wizard and VLAN Routing. Leave those pages unused.

### Rollback

If the web interface still opens, put membership and PVID back to the values you wrote down, Apply, and confirm the web interface opens again.

If you need the saved file, restore it with the Maintenance procedure in the GS724Tv4 manual. The backup direction is called Upload in the manual. The restore action is the matching download in that same Maintenance chapter. Use the label printed in the manual.

If the web interface is gone, use the console or factory-reset procedure in that same manual as a last resort. Factory defaults return the management address to `192.168.0.239` and remove the VLAN changes. Restore the saved configuration after you can log in again. Do not change the router DHCP service to recover the switch.

## 5. GitHub Actions Secrets and Variables

Configure **repository-level** settings on GitHub under **Settings → Secrets and variables → Actions**:

- In **Secrets**, select **New repository secret** for entries marked Secret.
- In **Variables**, select **New repository variable** for entries marked Variable.

Use the names exactly as written. Network topology and disk identity must be Secrets; putting them in Variables does not satisfy validation. `SSH_PUBLIC_KEY` is a Variable. Optional Variables may be omitted: the checker applies their defaults automatically. All 11 network Secrets are required without defaults, including both VLAN IDs. Documentation examples are not saved values.

| Name | GitHub storage | Description / value to enter | Required | Default value |
| --- | --- | --- | --- | --- |
| `SSH_PUBLIC_KEY` | Variable | Complete OpenSSH public `.pub` contents, one key per line; not a SHA256 fingerprint or private key. | Yes | None |
| `MANAGEMENT_INTERFACE_MAC` | Secret | MAAS server physical Ethernet MAC, such as `aa:bb:cc:dd:ee:ff`. | Yes | None |
| `MANAGEMENT_ADDRESS` | Secret | Unused fixed server IPv4 address on the management LAN, including CIDR prefix, such as `192.0.2.10/24`. | Yes | None |
| `MANAGEMENT_GATEWAY` | Secret | Existing router IPv4 address inside the management subnet. | Yes | None |
| `MANAGEMENT_DNS` | Secret | Nonempty JSON array of reachable IPv4 DNS resolvers, such as `["192.0.2.1"]`. | Yes | None |
| `MANAGEMENT_VLAN_ID` | Secret | Existing untagged management VLAN/PVID as an integer from 1 to 4093. | Yes | None |
| `PROVISIONING_VLAN_ID` | Secret | Separate provisioning VLAN ID as an integer from 4 to 4093, such as `100`. | Yes | None |
| `PROVISIONING_SUBNET` | Secret | Provisioning IPv4 network in CIDR form, such as `198.51.100.0/24`; must not overlap the management subnet. | Yes | None |
| `PROVISIONING_CONTROLLER_ADDRESS` | Secret | Server IPv4 address on the provisioning subnet, without a prefix and outside the DHCP pool. | Yes | None |
| `PROVISIONING_DHCP_START` | Secret | First dynamic IPv4 address in the provisioning subnet. | Yes | None |
| `PROVISIONING_DHCP_END` | Secret | Last dynamic IPv4 address in the provisioning subnet; greater than the start address. | Yes | None |
| `PROVISIONING_TEST_HOST` | Secret | Temporary wired validation host IPv4 address in the provisioning subnet, outside the pool and different from the controller. | Yes | None |
| `DISK_SERIAL` | Secret | Target disk short serial reported by `lsblk`; copy it exactly. | At least one disk ID | None |
| `DISK_WWN` | Secret | Target disk WWN reported by `lsblk`. | At least one disk ID | None |
| `DISK_BY_ID` | Secret | Target disk stable path starting with `/dev/disk/by-id/`. | At least one disk ID | None |
| `HOSTNAME` | Variable | Controller hostname as a single DNS label. | No | `maas-controller` |
| `TIMEZONE` | Variable | IANA timezone name. | No | `Europe/Paris` |
| `LOCALE` | Variable | Ubuntu locale supported by the pinned installation image. | No | `en_US.UTF-8` |
| `KEYBOARD_LAYOUT` | Variable | XKB keyboard layout; `fr` selects French. | No | `fr` |
| `KEYBOARD_VARIANT` | Variable | XKB keyboard variant; French legacy is `latin9` with layout `fr`. | No | `latin9` |
| `ADMIN_USER` | Variable | Linux administrative account used for SSH and sudo; its password is locked. | No | `maasadmin` |
| `MAAS_ADMIN_USERNAME` | Variable | MAAS web administrator username, separate from the Linux account. | No | `admin` |
| `MAAS_ADMIN_EMAIL` | Variable | Email address attached to the MAAS administrator account. | No | `admin@maas.local` |
| `POSTGRES_DATABASE` | Variable | Local PostgreSQL database name used by MAAS. | No | `maas` |
| `POSTGRES_USER` | Variable | Local PostgreSQL role used by MAAS. | No | `maas` |
| `BACKUP_DIRECTORY` | Variable | Absolute directory on the controller for backup dump/configuration bundle pairs. | No | `/var/backups/maas` |
| `BACKUP_RETAIN` | Variable | Number of completed backup pairs to retain, as an integer from 1 to 365. | No | `14` |

For `DISK_SERIAL`, `DISK_WWN`, and `DISK_BY_ID`, supply **at least one**, not all three. Leave unused alternatives absent. If you supply several, they must identify the same physical disk. On the target server's Ubuntu installer shell, inspect the disk with:

```bash
lsblk -d -o NAME,MODEL,SERIAL,WWN,SIZE,TYPE
ls -l /dev/disk/by-id/
```

For `SSH_PUBLIC_KEY`, read an existing public key on your Windows workstation, adjusting the filename if needed:

```powershell
Get-Content "$env:USERPROFILE\.ssh\id_ed25519.pub"
```

Copy the full line beginning with `ssh-ed25519`, `ssh-rsa`, or another supported public key type. A line beginning with `256 SHA256:` is a fingerprint and cannot be used as the key.

After entering the required values, run **Actions → Check installation variables → Run workflow**. It checks missing inputs, disk alternatives, defaults, and network consistency. Future ISO builds embed the resolved settings; changing GitHub settings does not update an already installed server. Booting the built ISO authorizes erasure of the identified target disk.

The MAAS and PostgreSQL passwords are generated on the server in `/etc/maas-provision/secrets.env`; do not create password Secrets for them. GS724Tv4 hardware assumptions, isolation policy, and explicit DHCP activation are fixed behavior, not optional inputs. See [the Actions configuration guide](docs/actions-variables.md) for migration and configuration helpers.

## 6. Architecture overview

The pipeline is:

```text
GitHub repository
    |
    v
GitHub Actions (workflow_dispatch)
    |  validates config, Ansible, and autoinstall
    |  downloads the official Ubuntu ISO and checks its SHA256
    v
ubuntu-maas-autoinstall.iso
    |
    v
USB boot on one bare-metal Ubuntu server
    |
    v
cloud-init autoinstall (one explicitly identified disk)
    |
    v
systemd first-boot service
    |
    v
local Ansible on that server
    |
    v
PostgreSQL 16 + MAAS 3.7 region+rack
    |
    v
DHCP remains disabled
    |
    v
on-site GS724T web configuration and VLAN acceptance
    |
    v
explicit MAAS DHCP activation on the provisioning VLAN only
```

There is one physical Ethernet port on the server. The management LAN and the provisioning VLAN share that port and the switch port it is plugged into.

```text
                         Internet
                            |
                      Existing router
                      DHCP: existing
                            |
                     Existing LAN
                            |
                  NETGEAR GS724Tv4
                    24-port switch
                            |
          +-----------------+-------------------+
          |                                     |
     Existing LAN                       VLAN 100 (example)
     Existing DHCP                      Provisioning network
          |                                     |
          |                            Bare-metal machines
          |
          +-------- MAAS server port -----------+
                            |
                    Single Ethernet NIC
                            |
                      Ubuntu Server
                        Netplan
                            |
                  +---------+----------+
                  |                    |
             Management          VLAN interface
               LAN                 VLAN 100
                  |                    |
                  +---------+----------+
                            |
                      MAAS 3.7
                    region + rack
                            |
                     PostgreSQL 16
```

The server switch port carries `MANAGEMENT_VLAN_ID` untagged and `PROVISIONING_VLAN_ID` tagged. Its PVID stays the management VLAN. Machine ports carry the provisioning VLAN untagged, and their PVID is the provisioning VLAN ID. The router-facing port stays as it is. The existing router DHCP configuration is not modified.

VLAN ID 100 and any addresses in examples are placeholders. The software always implements separate management and provisioning VLANs. Moving DHCP onto the existing LAN would need a separate, approved migration plan.

### Routing decision

The implemented decision is an isolated provisioning VLAN:

- Isolation is fixed policy, without routing or NAT configuration options.
- The server does not forward packets and is not the default gateway.
- The provisioning VLAN has no default route.
- MAAS clears provisioning gateway and DNS options on every configuration run.

These are four different paths:

| Traffic | Path |
|---|---|
| Internet access for the MAAS controller | Untagged management LAN, default route via the existing router |
| MAAS image synchronization | The controller downloads images over that management path |
| PXE, DHCP, TFTP, and commissioning | Provisioning VLAN only. Machines use the MAAS rack on the provisioning address. They do not need a default route for this |
| Internet access for a deployed machine | None, while the machine stays on the provisioning VLAN |

Keep the provisioning VLAN isolated. When a deployed machine needs general network access, change that machine's switch port back to the management LAN in the web interface and give the machine an address that belongs on that LAN. That is a separate change. Do not enable these alternatives unless a later design explicitly approves them:

1. Inter-VLAN routing on the existing router. This deployment does not add the provisioning VLAN to the router port.
2. GS724Tv4 Layer 3 routing. The administration manual documents Routing, VLAN, VLAN Routing Wizard and VLAN Routing. Those pages stay unused.
3. NAT or IP forwarding on Ubuntu. The host sysctl keeps `ip_forward` at 0; there is no installation option to enable it.

## 7. Writing the USB bootable installer

Use a USB disk you can overwrite. Either tool writes the ISO image as a whole disk image so the El Torito and EFI boot images stay intact.

Rufus on Windows:

1. Select the USB drive ;
2. Select `ubuntu-maas-autoinstall.iso`;
3. If Rufus offers ISO mode and DD mode, choose DD mode. DD mode copies the ISO byte for byte, preserving its boot partitions, EFI loader, and customized GRUB configuration. ISO mode extracts the files and rebuilds the USB’s boot structure. The latter can work, but can introduce changes made by Rufus in the installation process ;
4. Start.

balenaEtcher on Windows, macOS, or Linux:

1. Flash from file, and select the ISO ;
2. Select the USB drive ;
3. Flash.

## 8. Validating the VLAN

After the physical switch configuration and controller installation, follow the [correlated capture/probe procedure](docs/review-fixes.md#revised-physical-acceptance-procedure). It requires a separate wired management observer and a temporary provisioning host statically configured with the `PROVISIONING_TEST_HOST` Secret address, Python 3 and `iputils-arping`. The observer needs the checkout's helper/package, `ip` and `tcpdump`.

The controller checks interface MAC/link/address/routes, bidirectional connectivity, actual VLAN ID, a fresh switch confirmation, a complete DHCP capture, and a management capture covering a one-use-token broadcast probe. Failed/short/stale captures, incorrect observer networks and missing evidence give FAIL or INCONCLUSIVE, never an empty PASS. A successful test establishes the observed broadcast behaviour for this configuration, not every possible network-security property.

Run `sudo maas-network-confirm-switch` only after inspecting VLAN membership and PVID in the switch UI. This confirmation is valid for 24 hours and the current configuration hash. Use `sudo maas-network-validate --isolation-report /tmp/isolation-report.json` for validation only, or use activation directly as described below. The script prints the temporary host's exact command, including a fresh `--token`, then waits for the matching completed observer report.

Validation is written to `/var/lib/maas-provision/vlan-validation.json`. Activation requires all critical checks to PASS, a matching configuration hash and a report less than two hours old. The underlying capture/probe must be correlated and less than five minutes old when validation runs.

## 9. Activating MAAS DHCP

Activation is phase B. Phase A has already installed MAAS and configured the range with DHCP left off.

```bash
sudo maas-network-activate --isolation-report /path/to/isolation-report.json
```

The command repeats the validation in [section 8](#8-validating-the-vlan). A `FAIL` or `INCONCLUSIVE` result stops the script before the prompt. On success it prints `PROVISIONING_VLAN_ID`, `PROVISIONING_SUBNET`, and the `PROVISIONING_DHCP_START`–`PROVISIONING_DHCP_END` range, and it states that the management VLAN and the router DHCP service are not changed. Type:

```text
ACTIVATE DHCP ON VLAN 100
```

Use the `PROVISIONING_VLAN_ID` value it prints if it differs from this example of 100. The command then runs `maas <profile> vlan update <fabric-id> <vlan-id> dhcp_on=true primary_rack=<system-id>` for the provisioning VLAN only. It checks `maas status` afterward and writes `/var/lib/maas-provision/dhcp-active` when `dhcpd` is running.

Emergency stop, including when you cannot complete a fresh validation:

```bash
sudo maas-network-deactivate --emergency
```

Type:

```text
EMERGENCY DEACTIVATE DHCP ON VLAN 100
```

The ordinary stop is `sudo maas-network-deactivate` and the phrase `DEACTIVATE DHCP ON VLAN 100`. Both call `vlan update` with `dhcp_on=false` for the provisioning VLAN. Neither command talks to the router, and neither enables DHCP on the management VLAN.

If activation fails, leave DHCP off, read `/var/log/maas-provision/`, and run `sudo maas status`. Do not widen the range onto the management subnet to "make PXE work".

## 10. Accessing the MAAS interface

From a browser on the management LAN, use the IPv4 part of the `MANAGEMENT_ADDRESS` Secret (omit its `/prefix`):

```text
http://<management-address>:5240/MAAS/
```

On the server console, `http://127.0.0.1:5240/MAAS/` also works. The URL stored in MAAS for enlisted machines is `http://<controller-address>:5240/MAAS/`, which is the `PROVISIONING_CONTROLLER_ADDRESS` Secret.

The username is the `MAAS_ADMIN_USERNAME` Variable (`admin` by default). The password is generated locally as `MAAS_ADMIN_PASSWORD` in `/etc/maas-provision/secrets.env`; it is not a GitHub Secret. The Linux SSH account is the separate `ADMIN_USER` Variable (`maasadmin` by default).

After login, confirm:

- Controllers: one controller, both region and rack.
- Subnets: `PROVISIONING_SUBNET`, with `PROVISIONING_CONTROLLER_ADDRESS` reserved and the dynamic range matching `PROVISIONING_DHCP_START`–`PROVISIONING_DHCP_END`.
- VLANs: DHCP enabled only after [section 9](#9-activating-maas-dhcp), and only on the provisioning VLAN.
- Images: Ubuntu Noble present and synced. `maas-images` records success in `/var/lib/maas-provision/images-synced`.

The controller itself reaches the image mirror through the management default route. Commissioning machines reach the downloaded images through the rack controller on the provisioning VLAN.

## 11. First PXE deployment

1. Connect the test machine to a provisioning port chosen in [section 3](#3-physical-port-mapping). That port must be untagged in `PROVISIONING_VLAN_ID`, with the same value as its PVID.
2. In the machine firmware, enable UEFI network boot and put it first.
3. Power the machine on.
4. On the MAAS server, confirm DHCP is active with `sudo maas status` and that `dhcpd` is running. If the machine receives no address, check its switch port membership/PVID and the DHCP activation status.
5. The machine should appear in MAAS as a newly enlisted machine. Accept or commission it from the web interface.
6. Commission the machine. Commissioning uses the rack controller on the provisioning network.
7. Select the synced Ubuntu image.
8. Deploy Ubuntu.
9. When deployment finishes, the machine's address is the one MAAS assigned from the provisioning range. From the MAAS server:

```bash
ping -I provisioning <deployed-address>
```

The deployed machine can reach the MAAS controller on the provisioning VLAN. It has no default route off that VLAN, because the routing decision is isolation. If the machine later needs the existing LAN or the Internet, move its switch port in the web interface as a separate change. Do not enable NAT on the MAAS server to paper over that.
