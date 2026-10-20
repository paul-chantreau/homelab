# Production MAAS on one Ubuntu server and a Netgear GS724Tv4

This repository builds an Ubuntu Server autoinstall ISO along with Ansible to install MAAS. It assumes the switch is a Netgear GS724Tv4 ; the configuration and procedures use that specific model.

## 1. Requirements

One dedicated bare-metal server:
- Exactly one physical Ethernet interface. Netplan selects it by MAC address.
- Enough disk space for Ubuntu, PostgreSQL, and MAAS images.
- A disk you can identify by serial, WWN, or a `/dev/disk/by-id/` path. The installer never picks the largest disk and never assumes `/dev/sda`.
- UEFI or legacy BIOS boot from USB.
- Out-of-band or local console access for setup and recovery. The installed account password is locked; normal administrative login uses an SSH public key.

You will also need an out-of-band or local console access to reach the Netgear GS724Tv4 management web interface on the existing LAN.

## 2. Architecture overview

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

The server switch port carries the management LAN untagged and the provisioning VLAN tagged. Its PVID stays the management VLAN. Machine ports carry the provisioning VLAN untagged, and their PVID is the provisioning VLAN ID. The router-facing port stays as it is. The existing router DHCP configuration is not modified.

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

## 3. Inspecting the existing network

Discovery is read-only. It records local addresses, routes, and DNS. It does not log in to the switch, and it cannot see VLAN membership, PVID, or which switch port a cable uses. A workstation on the LAN cannot discover the complete switch configuration.

On a Linux machine on the existing LAN, from a checkout of this repository:

```bash
scripts/discover-network.sh
```

The script runs `ip -j address`, `ip -j link`, `ip -j route`, `networkctl status`, and `resolvectl status`. It writes `network-discovery.json` and `network-discovery.txt` in the current directory. The summary states the limitation above and lists interfaces and routes.

On Windows PowerShell, the equivalent collection is `scripts/discover-network.ps1`, which runs:

```powershell
Get-NetAdapter
Get-NetIPConfiguration
Get-NetRoute
Get-DnsClientServerAddress
```

Collect the following by looking at the router and the switch, not by guessing from a host address:

| Item | Where to read it |
|---|---|
| Router IP address | The default gateway on a LAN host, then confirm it in the router UI |
| Existing DHCP server | Router UI. Do not change it. A host cannot reliably name the DHCP server from its own lease alone |
| Existing DHCP scope | Router DHCP settings. Copy the start, end, and subnet before any switch work |
| Current LAN subnet | An address and prefix on a LAN host, confirmed against the router |
| Netgear management IP | Router DHCP lease table; match the MAC address printed on the switch label |
| Physical switch port mapping | Front-panel link LEDs, [section 5](#5-physical-port-mapping) |
| Server NIC MAC address | `ip link` on the server, or the label on the NIC. This becomes `network.management.interface_mac` |

Do not infer VLAN membership from the fact that a host has an address. Untagged traffic on the management VLAN looks the same to the host as an untagged flat LAN.

## 4. Configuring VLAN 100 on the GS724Tv4

This section is the on-site procedure. It requires the physical switch. A passing Ubuntu VLAN interface does not mean these pages were set correctly.

Use VLAN ID 100. The validator rejects VLANs 1, 2, and 3 because the GS724Tv4 manual predefines them and they cannot be deleted.

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
7. Re-open VLAN Membership and Port PVID Configuration. Compare them with the worksheet in [section 5](#5-physical-port-mapping).
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

## 5. Physical port mapping

Port numbers are whatever is printed on the front of the switch. Find each one by watching link LEDs: disconnect one cable, record the port whose LED goes out, reconnect it, and continue. Do not reuse an example number from this document.

Optionally copy this worksheet into private local notes and replace every "User-defined" cell. It is an operator aid, not an installation input: port numbers do not belong in YAML or GitHub Actions Secrets. Identifying and inspecting the connected ports is still required. The management VLAN column uses the ID you recorded, which is often 1 and is not automatically 1.

| Device | Switch port | VLAN membership | PVID |
|---|---|---|---|
| Router | User-defined | Management LAN, unchanged | Management VLAN |
| MAAS server | User-defined | Management U + Provisioning T | Management VLAN |
| Machine 1 | User-defined | Provisioning U | Provisioning VLAN |
| Machine 2 | User-defined | Provisioning U | Provisioning VLAN |
| Additional machines | User-defined | Provisioning U | Provisioning VLAN |

A port may appear in only one role. The GS724Tv4 has 24 copper ports and two SFP ports, numbered 1–26. Software does not configure or query these ports.

`sudo maas-network-confirm-switch` prints the required membership and PVID for each role, using the configured VLAN IDs. It asks for confirmation after you inspect the actual connected ports in the VLAN Membership and Port PVID pages. A worksheet is not required by this command.

## 6. Writing the USB bootable installer

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

## 7. Validating the VLAN

After the physical switch configuration and controller installation, follow the [correlated capture/probe procedure](docs/review-fixes.md#revised-physical-acceptance-procedure). It requires a separate wired management observer and a temporary provisioning host with the configured static test address, Python 3 and `iputils-arping`. The observer needs the checkout's helper/package, `ip` and `tcpdump`.

The controller checks interface MAC/link/address/routes, bidirectional connectivity, actual VLAN ID, a fresh switch confirmation, a complete DHCP capture, and a management capture covering a one-use-token broadcast probe. Failed/short/stale captures, incorrect observer networks and missing evidence give FAIL or INCONCLUSIVE, never an empty PASS. A successful test establishes the observed broadcast behaviour for this configuration, not every possible network-security property.

Run `sudo maas-network-confirm-switch` only after inspecting VLAN membership and PVID in the switch UI. This confirmation is valid for 24 hours and the current configuration hash. Use `sudo maas-network-validate --isolation-report /tmp/isolation-report.json` for validation only, or use activation directly as described below. The script prints the temporary host's exact command, including a fresh `--token`, then waits for the matching completed observer report.

Validation is written to `/var/lib/maas-provision/vlan-validation.json`. Activation requires all critical checks to PASS, a matching configuration hash and a report less than two hours old. The underlying capture/probe must be correlated and less than five minutes old when validation runs.

## 8. Activating MAAS DHCP

Activation is phase B. Phase A has already installed MAAS and configured the range with DHCP left off.

```bash
sudo maas-network-activate --isolation-report /path/to/isolation-report.json
```

The command repeats the validation in [section 7](#7-validating-the-vlan). A `FAIL` or `INCONCLUSIVE` result stops the script before the prompt. On success it prints the VLAN, subnet, and dynamic range, and it states that the management VLAN and the router DHCP service are not changed. Type:

```text
ACTIVATE DHCP ON VLAN 100
```

Use the VLAN ID it prints if you did not keep 100. The command then runs `maas <profile> vlan update <fabric-id> <vlan-id> dhcp_on=true primary_rack=<system-id>` for the provisioning VLAN only. It checks `maas status` afterward and writes `/var/lib/maas-provision/dhcp-active` when `dhcpd` is running.

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

## 9. Accessing the MAAS interface

From a browser on the management LAN:

```text
http://<management-address>:5240/MAAS/
```

On the server console, `http://127.0.0.1:5240/MAAS/` also works. The URL stored in MAAS for enlisted machines is `http://<controller-address>:5240/MAAS/`, which is the provisioning address.

The username is `deployment.maas.admin_username` (`admin` unless you changed it). The password is `MAAS_ADMIN_PASSWORD` in `/etc/maas-provision/secrets.env`.

After login, confirm:

- Controllers: one controller, both region and rack.
- Subnets: the provisioning subnet, with the controller address reserved and the dynamic range matching the YAML.
- VLANs: DHCP enabled only after [section 8](#8-activating-maas-dhcp), and only on the provisioning VLAN.
- Images: Ubuntu Noble present and synced. `maas-images` records success in `/var/lib/maas-provision/images-synced`.

The controller itself reaches the image mirror through the management default route. Commissioning machines reach the downloaded images through the rack controller on the provisioning VLAN.

## 10. First PXE deployment

1. Connect the test machine to a GS724T provisioning port from the worksheet. That port is untagged VLAN 100 with PVID 100.
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
