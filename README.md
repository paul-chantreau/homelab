# Production MAAS on one Ubuntu server and a Netgear GS724Tv4

This repository builds an Ubuntu Server autoinstall ISO, boots it from USB, and lets Ansible on that machine install MAAS. It assumes the switch is a Netgear GS724Tv4 ; the configuration and procedures use that fixed model.

Record all of the following before changing a port, building an ISO, or installing Ubuntu:

1. Switch management IP address.
2. Physical port numbers for the router, the MAAS server, and each future machine.
3. Existing VLAN membership and PVID of every port you might touch.
4. Existing switch management VLAN.

The software assumes a GS724Tv4. `config/network.yaml` contains only 11 site-specific inputs, all supplied by required GitHub Actions Secrets during a build. Physical addresses stay null until you inspect the switch and the LAN. The installer refuses to erase a disk until the required site settings are filled in.

The procedure uses the GS724Tv4 administration manual (25 September 2013, covers GS716Tv3, GS724Tv4, and GS748Tv5): <https://www.downloads.netgear.com/files/GDC/GS716TV3/GS716Tv3_GS724Tv4_GS748Tv5_SWA_25Sept2013.pdf>

The current product page may feature a newer revision: <https://www.netgear.com/business/wired/switches/smart-cloud/gs724t/>. Use the 2013 manual above, not the manual highlighted for a later switch.

This article does not apply to the GS724Tv4 and must not be used as the procedure: <https://kb.netgear.com/31026/How-to-configure-a-VLAN-on-a-NETGEAR-managed-switch>. It describes a different managed-switch family. Its menu puts VLAN creation under Switching, VLAN, Advanced. The GS724Tv4 manual puts VLAN creation under Switching, VLAN, Basic.

Virtual tests in this repository do not configure a GS724T and do not prove that VLAN 100 is isolated. That check happens on site, after the web interface has been changed, using the procedure in section M.

## A. Architecture overview

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

For fewer than 10 machines, keep the provisioning VLAN isolated. When a deployed machine needs general network access, change that machine's switch port back to the management LAN in the web interface and give the machine an address that belongs on that LAN. That is a separate change. Do not enable these alternatives unless a later design explicitly approves them:

1. Inter-VLAN routing on the existing router. This deployment does not add the provisioning VLAN to the router port.
2. GS724Tv4 Layer 3 routing. The administration manual documents Routing, VLAN, VLAN Routing Wizard and VLAN Routing. Those pages stay unused.
3. NAT or IP forwarding on Ubuntu. The host sysctl keeps `ip_forward` at 0; there is no installation option to enable it.

## B. Hardware prerequisites

One dedicated bare-metal server:

- Ubuntu Server 24.04.5 LTS, installed by the autoinstall ISO.
- Exactly one physical Ethernet interface. Netplan selects it by MAC address.
- Enough disk for Ubuntu, PostgreSQL, and MAAS images. The health check reports failure at 80 percent full on `/`.
- A disk you can identify by serial, WWN, or a `/dev/disk/by-id/` path. The installer never picks the largest disk and never assumes `/dev/sda`.
- UEFI or legacy BIOS boot from USB. The ISO keeps the Ubuntu El Torito image, protective MBR, and appended EFI partition.
- Out-of-band or local console access. The installed account password is locked. Administrative login is an SSH public key, or the console recovery in section K.

One Netgear GS724Tv4:

- IEEE 802.1Q VLANs, configured only through the web interface.
- Ports 1–26 (24 copper and 2 SFP), up to 256 VLANs. VLANs 1, 2, and 3 are predefined and cannot be deleted. Ports start as untagged members of those three.
- VLAN 100 is a valid provisioning VLAN. It is not one of the predefined VLANs.
- A management web interface reachable from the existing LAN.

Other requirements:

- Fewer than 10 machines to provision.
- An existing LAN and router. Leave the router's DHCP service and scope alone.
- No second switch and no remote Ansible controller.
- Machines that can UEFI network-boot (PXE) on the provisioning ports.
- Packer, Terraform, Docker, Kubernetes, LXD, and a remote Ansible controller are not part of this design. The MAAS snap may pull optional LXD components; this deployment does not use LXD and does not configure it.

One MAAS controller is a single point of failure. If this server is down, new DHCP, PXE, commissioning, and deployment stop. Machines that were already deployed keep running only to the extent that they no longer need MAAS at runtime. There is no second region or rack controller.

## C. Inspecting the existing network

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
| Netgear management IP | Section E |
| Physical switch port mapping | Front-panel link LEDs, section G |
| Server NIC MAC address | `ip link` on the server, or the label on the NIC. This becomes `network.management.interface_mac` |

Do not infer VLAN membership from the fact that a host has an address. Untagged traffic on the management VLAN looks the same to the host as an untagged flat LAN.

## D. GS724Tv4 switch assumptions

The switch is assumed to be a Netgear GS724Tv4. Use the web-interface procedures from its administration manual. Open the web interface (section E) and record:

- Management IP address.
- Management VLAN. It is configured under System, Management, IP Configuration. Leave it unchanged.
- Current VLAN membership and PVID, using the pages in section F.

## E. Accessing the GS724Tv4 web interface

Find the management IP from one of these sources, in order:

1. The DHCP lease table on the existing router. Match the MAC address printed on the switch label.
2. The Smart Control Center described in the GS724Tv4 administration manual. Use it only to display the address.
3. The factory address `192.168.0.239` when the switch has never obtained a lease and is still at defaults. A host must be addressed on `192.168.0.0/24` to open that address. Do not change the router to create that path if the switch is already reachable another way.

Open `http://<management-ip>/` in a browser on the management LAN. Log in with the local administrator account. This repository does not store that password.

Before any VLAN change:

1. Export the current configuration: Maintenance, Upload, HTTP File Upload, file type Text Configuration. On this switch, Upload means a copy that leaves the switch. Keep that file off the switch.
2. Write down every VLAN, which ports are T, U, or blank, and every port PVID.
3. Write down the management VLAN from System, Management, IP Configuration.
4. Identify the router port, the MAAS server port, and the machine ports with the LED method in section G. Do not invent port numbers.
5. Confirm you can still open the web interface after the backup and before you change membership.

Changing the management VLAN drops the current session. This procedure does not change it.

## F. Configuring VLAN 100 on the GS724Tv4

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

1. Confirm the backup from section E opens, and confirm the management VLAN ID.
2. Create VLAN 100. Do not edit port membership yet. Apply. Confirm the VLAN is in the VLAN list. Confirm the web interface still loads.
3. On the MAAS server port only, add tagged membership in VLAN 100. Leave its untagged management membership and its PVID as they are. Apply. Open the web interface again.
4. On one machine port, remove untagged membership in the management VLAN. Do not leave that port untagged in VLAN 2 or VLAN 3 either. Add untagged membership in VLAN 100. Set that port's PVID to 100. Apply. Confirm the web interface still loads from the management LAN.
5. Repeat step 4 for each remaining machine port.
6. Do not change the router port.
7. Re-open VLAN Membership and Port PVID Configuration. Compare them with the worksheet in section G.
8. If the GS724Tv4 manual requires a separate write of the running configuration to non-volatile memory, do that step from the manual after Apply. Re-open the pages and confirm the values are still there.

### Menus

From the 25 September 2013 administration manual:

1. Switching, VLAN, Basic, VLAN Configuration. Create VLAN 100. Apply. Do not delete VLAN 1, 2, or 3.
2. Switching, VLAN, Advanced, VLAN Membership. Machine ports must not remain untagged members of VLAN 1, 2, or 3 once they are untagged in VLAN 100, because a port is untagged in only one VLAN. The MAAS port stays untagged in the management VLAN and tagged in VLAN 100. Apply.
3. Switching, VLAN, Advanced, Port PVID Configuration. Machine ports use PVID 100. The MAAS port keeps the management PVID. Apply.

The manual documents Routing, VLAN, VLAN Routing Wizard and VLAN Routing. Leave those pages unused.

### Rollback

If the web interface still opens, put membership and PVID back to the values you wrote down, Apply, and confirm the web interface opens again.

If you need the saved file, restore it with the Maintenance procedure in the GS724Tv4 manual. The backup direction verified above is Upload. The restore action is the matching download in that same Maintenance chapter. Use the label printed in the manual.

If the web interface is gone, use the console or factory-reset procedure in that same manual as a last resort. Factory defaults return the management address to `192.168.0.239` and remove the VLAN changes. Restore the saved configuration after you can log in again. Do not change the router DHCP service to recover the switch.

## G. Physical port mapping

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

## H. Preparing configuration files

Network addresses exist only in `config/network.yaml`. Deployment choices exist only in `config/deployment.yaml`. The installer, Netplan, and MAAS network plan are generated from those files. Null means unknown. Do not invent an address to make the file look finished, and do not put passwords in either file.

Check the committed files without requiring real addresses:

```bash
python3 scripts/validate-config.py --mode schema \
  --network config/network.yaml \
  --deployment config/deployment.yaml
```

The installation mode refuses nulls and unsafe combinations:

```bash
python3 scripts/validate-config.py --mode install \
  --network config/network.yaml \
  --deployment config/deployment.yaml
```

The committed files pass schema mode and fail install mode. That is intentional.

### `config/network.yaml`

| Field | Meaning |
|---|---|
| `network.management.interface_mac` | Server NIC MAC, `aa:bb:cc:dd:ee:ff`, unicast |
| `network.management.address` | Server address on the management LAN, CIDR, such as a real `192.0.2.10/24` only if that is your chosen address |
| `network.management.gateway` | Existing router address. It must sit inside the management subnet and must not be the server address |
| `network.management.dns` | Resolver addresses reachable from the management LAN. At least one in install mode |
| `network.management.vlan_id` | Existing management VLAN ID. Default placeholder is 1. Set the real PVID. Do not change the switch to match the file |
| `network.provisioning.vlan_id` | Provisioning VLAN. Placeholder 100 |
| `network.provisioning.subnet` | Provisioning subnet in CIDR form. Must not overlap the management subnet |
| `network.provisioning.controller_address` | Server address on that VLAN. Host address only, inside the subnet, outside the DHCP pool, and not the network or broadcast address |
| `network.provisioning.dhcp.range_start` | First dynamic address. Inside the subnet, before `range_end`, not the network or broadcast address |
| `network.provisioning.dhcp.range_end` | Last dynamic address |
| `network.provisioning.acceptance.test_host` | Static address of the temporary test host, inside the provisioning subnet, outside the DHCP pool |

Install mode also checks IPv4 and CIDR syntax, different management/provisioning VLAN IDs, and the GS724Tv4 reserved VLANs 1, 2, and 3. Each of the 11 fields above maps to a required Actions Secret listed in [Actions configuration](docs/actions-variables.md). DHCP starts disabled and requires a separate, confirmed activation.

For an older manually edited YAML, remove `network.mode`, `network.routing`, `network.switch`, `network.provisioning.dhcp.activate_automatically`, `network.provisioning.gateway`, and `network.provisioning.dns`. Preserve the 11 site inputs above. Obsolete fields are rejected rather than silently ignored. Revalidate before installation or a playbook rerun. The changed configuration hash requires a fresh switch confirmation and acceptance session. Rebuilding from Actions uses the updated template automatically.

### `config/deployment.yaml`

| Field | Meaning |
|---|---|
| `deployment.hostname` | Server hostname. Default `maas-controller` |
| `deployment.timezone` | IANA timezone. Default `Etc/UTC` |
| `deployment.locale` | Default `en_US.UTF-8` |
| `deployment.keyboard_layout` | Default `us` |
| `deployment.keyboard_variant` | XKB variant; default `latin9` with layout `fr` |
| `deployment.admin_user` | Local Linux account. Not `root`, `maas`, or `postgres`. Default `maasadmin` |
| `deployment.ssh_authorized_keys` | One or more SSH public keys. Install mode rejects an empty list and rejects a private key |
| `deployment.disk.serial` | Disk serial, from `lsblk` |
| `deployment.disk.wwn` | Disk WWN, if you use that instead of or in addition to the serial |
| `deployment.disk.by_id` | A `/dev/disk/by-id/` path. Bare names such as `/dev/sda` and `nvme0n1` are rejected |
| `deployment.disk.model` | Optional extra constraint. Model alone is never enough |
| `deployment.disk.confirm_erase` | Must be `true` only after you have written down which physical disk will be erased |
| `deployment.ubuntu` | Pinned to Ubuntu 24.04.5 and the published SHA256 of `ubuntu-24.04.5-live-server-amd64.iso`. Do not point this at a different ISO |
| `deployment.maas.channel` | `3.7/stable` |
| `deployment.maas.mode` | `region+rack` |
| `deployment.maas.admin_username` | MAAS web and CLI user. Default `admin` |
| `deployment.maas.admin_email` | MAAS admin email |
| `deployment.maas.image_release` | `noble` |
| `deployment.postgresql.major` | `16`, from the Ubuntu archive, not the MAAS test database and not the PostgreSQL snap |
| `deployment.postgresql.database` | Database name `maas` |
| `deployment.postgresql.user` | Database role `maas` |
| `deployment.backup.directory` | Local dump directory. Default `/var/backups/maas` |
| `deployment.backup.offhost_target` | Optional rsync destination such as `backup@fileserver:/srv/maas`. Null keeps dumps on the server. Do not put a password here |
| `deployment.backup.retain` | How many local dumps to keep. Default 14 |

Identify the disk on the target server before you set `confirm_erase`:

```bash
lsblk -d -o NAME,MODEL,SERIAL,WWN,SIZE,TYPE
ls -l /dev/disk/by-id/
```

Every rendered ISO starts with an impossible disk selector. The early-command verifies exactly one unmounted disk using the configured serial, WWN and/or by-id alias, excludes mounted installation media, then binds Subiquity to that verified device's exact runtime path in `/autoinstall.yaml`. Kernel names are never accepted as user-provided identity. Keep the hardware's short serial in `DISK_SERIAL`; no manual conversion to a udev serial is needed. Missing, ambiguous or mounted targets stop installation before partitioning.

### What install mode generates

Netplan matches the configured MAC, names the logical interface `maas_nic`, and names the VLAN interface `provisioning`. The kernel name is not assumed to be `enp1s0`. DHCP and router advertisements are off on both interfaces. The default route uses the management gateway. The VLAN stanza has an address and no routes. This shape is an illustration with documentation addresses, not the committed file:

```yaml
network:
  version: 2
  renderer: networkd
  ethernets:
    maas_nic:
      match:
        macaddress: "52:54:00:12:34:56"
      dhcp4: false
      addresses:
        - 192.0.2.10/24
      routes:
        - to: default
          via: 192.0.2.1
      nameservers:
        addresses:
          - 192.0.2.1
  vlans:
    provisioning:
      id: 100
      link: maas_nic
      dhcp4: false
      addresses:
        - 198.51.100.2/24
```

Use your own subnets. `192.0.2.0/24` and `198.51.100.0/24` are documentation ranges used by the tests.

## I. Building the ISO with GitHub Actions

The workflow is `.github/workflows/build-iso.yml`. It requests `contents: read` and uses pinned checkout and artifact-upload actions. Repository Actions **Secrets** supply network topology and disk identities. Non-sensitive preferences and `SSH_PUBLIC_KEY` remain in **Variables**. MAAS and PostgreSQL passwords are generated on the installed server, never in GitHub.

See [GitHub Actions variables](docs/actions-variables.md) for the required/defaulted list and helpers to migrate topology Variables to Secrets and create missing public Variables without overwriting existing values. Defaults include `Europe/Paris` and French legacy keyboard (`fr`, variant `latin9`). Run **Check installation variables** to check the current configuration independently of ISO construction.

The build validates all required Secrets/Variables and at least one disk identifier before downloading the Ubuntu ISO. It writes the resolved configuration into the runner checkout so the ISO and installed server use identical values. There is no disk-confirmation workflow input: the manual build prepares an ISO authorized to erase the explicitly identified disk. The physical installer still requires exactly one matching disk before partitioning. Switch and DHCP activation remain on-site operations.

On a pull request, the `validate` job checks out the branch, installs Ansible and pytest, runs schema validation, `pytest`, `ansible-playbook --syntax-check`, and `ansible-lint`.

The ISO job runs only for a manual `workflow_dispatch`:

1. Open the Actions tab for this repository.
2. Choose "Build MAAS autoinstall ISO".
3. Fill the repository Actions Secrets and Variables, then manually run the workflow on the desired branch. An incomplete or inconsistent configuration stops the job before the ISO download.
4. The job downloads `ubuntu-24.04.5-live-server-amd64.iso` from `https://releases.ubuntu.com/24.04.5/` and checks it against the published `SHA256SUMS` and against `deployment.ubuntu.sha256`.
5. It injects this repository, the rendered autoinstall files, and a patched GRUB configuration. The volume label stays `Ubuntu-Server 24.04.5 LTS amd64`.
6. Download the `ubuntu-maas-autoinstall` artifact. It contains `ubuntu-maas-autoinstall.iso`, `SHA256SUMS`, and `build-manifest.json`. The artifact is kept for 14 days.

On a machine with the three files in one directory:

```bash
sha256sum -c SHA256SUMS
python3 -m json.tool build-manifest.json
```

`build-manifest.json` records the artifact SHA256, the source ISO digest, whether `install_ready` is true, the MAAS channel, the PostgreSQL major version, and the hash of the configuration that was injected. If `install_ready` is false, the ISO boots to GRUB and then refuses to erase a disk. Finish the YAML and run the workflow again.

A local build uses the same script. It caches the upstream ISO under `build/cache/` and writes the artifact next to `build/SHA256SUMS`:

```bash
scripts/build-iso.sh
sha256sum -c build/SHA256SUMS
```

The local build needs `xorriso`, `curl`, and the Python packages listed in `requirements-dev.txt` (`PyYAML`, `jsonschema`, `Jinja2`). For a local build, fill `deployment.ssh_authorized_keys` in `config/deployment.yaml`; local builds do not read GitHub Actions variables.

## J. Writing the USB bootable installer

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

## K. Installing Ubuntu

Boot the server from the USB drive. The GRUB menu offers "Try or Install Ubuntu Server" and "Ubuntu Server with the HWE kernel". Both kernel lines contain:

```text
autoinstall ds=nocloud\;s=/cdrom/maas-deploy/autoinstall/
```

The default entry starts after 30 seconds. Before any partition is written, `scripts/preinstall-check.py` loads the injected YAML. It prints `The installer will erase this disk:` and the serial, WWN, model, and size of the one matching disk. The storage layout is `direct` on that disk, which removes its existing partitions and installs Ubuntu there. If the check fails, the screen says no disk was erased and the installer stops.

The Linux account named in `deployment.admin_user` receives the SSH public keys from the YAML. Password authentication is disabled. The account password is a random hash generated at ISO build time and then locked with `passwd -l`. That hash is not a credential. There is no password to memorize, and there is no private key on the ISO.

Normal administration is SSH from a management-LAN host whose private key matches `ssh_authorized_keys`:

```bash
ssh maasadmin@<management-address>
```

Local recovery when SSH is unavailable:

1. Reboot and stop at the installed system's GRUB menu.
2. Press `e` on the Ubuntu entry.
3. On the `linux` line, append a space and `init=/bin/bash`.
4. Boot with Ctrl-X or F10.
5. At the shell, run `mount -o remount,rw /`.
6. Read `/etc/maas-provision/secrets.env` if provisioning has already created it, or set a temporary password with `passwd maasadmin` and unlock the account with `passwd -u maasadmin`.
7. Continue with `exec /sbin/init` or `reboot -f`.

Remove the temporary password and lock the account again after SSH works: `sudo passwd -l maasadmin`.

## L. First-boot provisioning

cloud-init copies the repository to `/opt/maas-deploy`, installs `maas-firstboot.service`, and links operator commands into `/usr/local/sbin`. On the first boot after install, that service runs `scripts/first-boot.sh`, which runs `ansible/install.yml` locally as root.

Watch it with:

```bash
sudo journalctl -u maas-firstboot.service -f
sudo tail -f /var/log/maas-provision/first-boot.log
sudo tail -f /var/log/maas-provision/ansible.log
sudo tail -f /var/log/cloud-init-output.log
```

The playbook runs these roles, in order: `system`, `network`, `security`, `postgresql`, `maas`, `maas-network`, `maas-images`, `backup`, `healthcheck`.

What they do:

- Install packages, set the hostname and timezone, enable unattended security updates without automatic reboot, and set `net.ipv4.ip_forward` and `net.ipv6.conf.all.forwarding` to 0.
- Render `/etc/netplan/99-maas-provision.yaml` from `config/network.yaml`, disable cloud-init networking, and apply Netplan.
- Disable SSH password authentication and root login.
- Install PostgreSQL 16 from Ubuntu and create the `maas` role and database once. Later runs do not drop the database or rotate the password.
- Install the MAAS snap from channel `3.7/stable` if it is absent, hold snap refresh, and run `maas init region+rack` once against `postgres://maas:...@127.0.0.1:5432/maas`. The advertised URL is `http://<controller-address>:5240/MAAS/`.
- Create the MAAS admin user once. Later runs do not reset that password.
- Create the provisioning subnet, a reserved range for the controller address, and the dynamic DHCP range. DHCP on that VLAN is set off. If the management VLAN were marked DHCP-on, the plan turns that flag off. The router is not contacted.
- Select and import the Ubuntu Noble image, then poll until MAAS reports that the import finished.
- Enable a daily backup timer and a health-check timer.

Success creates `/var/lib/maas-provision/complete`. A failed run leaves that file absent and exits non-zero. Fix the cause and start the service again:

```bash
sudo systemctl start maas-firstboot.service
```

The service does not start a second time after the marker exists. To apply the playbook again on purpose:

```bash
cd /opt/maas-deploy/ansible
sudo ansible-playbook install.yml
```

That rerun is the supported path. It does not recreate PostgreSQL and does not reset the MAAS admin password. Deleting the completion marker and starting the service is equivalent, and it is also safe for the same reason.

Credentials are created on the server in `/etc/maas-provision/secrets.env` with mode `0600`. The keys are `POSTGRES_PASSWORD`, `MAAS_ADMIN_PASSWORD`, and, after login, `MAAS_API_KEY`. Read the file from the local console or from an SSH session as root. It is not in Git, not in the ISO, and not printed by the scripts (`set +x`) or by the Ansible tasks that handle it (`no_log`). During the first `maas createadmin`, the password is passed on that process's command line. Keep untrusted users off the console until the first boot has finished.

PostgreSQL and MAAS logs:

```bash
sudo journalctl -u postgresql
sudo journalctl -u snap.maas.pebble.service
sudo maas status
```

`maas status` should show `regiond` and `rackd` running. `dhcpd` should not be running yet.

## M. Validating the VLAN

After the physical switch configuration and controller installation, follow the [correlated capture/probe procedure](docs/review-fixes.md#revised-physical-acceptance-procedure). It requires a separate wired management observer and a temporary provisioning host with the configured static test address, Python 3 and `iputils-arping`. The observer needs the checkout's helper/package, `ip` and `tcpdump`.

The controller checks interface MAC/link/address/routes, bidirectional connectivity, actual VLAN ID, a fresh switch confirmation, a complete DHCP capture, and a management capture covering a one-use-token broadcast probe. Failed/short/stale captures, incorrect observer networks and missing evidence give FAIL or INCONCLUSIVE, never an empty PASS. A successful test establishes the observed broadcast behaviour for this configuration, not every possible network-security property.

Run `sudo maas-network-confirm-switch` only after inspecting VLAN membership and PVID in the switch UI. This confirmation is valid for 24 hours and the current configuration hash. Use `sudo maas-network-validate --isolation-report /tmp/isolation-report.json` for validation only, or use activation directly as described below. The script prints the temporary host's exact command, including a fresh `--token`, then waits for the matching completed observer report.

Validation is written to `/var/lib/maas-provision/vlan-validation.json`. Activation requires all critical checks to PASS, a matching configuration hash and a report less than two hours old. The underlying capture/probe must be correlated and less than five minutes old when validation runs.

## N. Activating MAAS DHCP

Activation is phase B. Phase A has already installed MAAS and configured the range with DHCP left off.

```bash
sudo maas-network-activate --isolation-report /path/to/isolation-report.json
```

The command runs section M again. A `FAIL` or `INCONCLUSIVE` result stops the script before the prompt. On success it prints the VLAN, subnet, and dynamic range, and it states that the management VLAN and the router DHCP service are not changed. Type:

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

## O. Accessing the MAAS interface

From a browser on the management LAN:

```text
http://<management-address>:5240/MAAS/
```

On the server console, `http://127.0.0.1:5240/MAAS/` also works. The URL stored in MAAS for enlisted machines is `http://<controller-address>:5240/MAAS/`, which is the provisioning address.

The username is `deployment.maas.admin_username` (`admin` unless you changed it). The password is `MAAS_ADMIN_PASSWORD` in `/etc/maas-provision/secrets.env`.

After login, confirm:

- Controllers: one controller, both region and rack.
- Subnets: the provisioning subnet, with the controller address reserved and the dynamic range matching the YAML.
- VLANs: DHCP enabled only after section N, and only on the provisioning VLAN.
- Images: Ubuntu Noble present and synced. `maas-images` records success in `/var/lib/maas-provision/images-synced`.

The controller itself reaches the image mirror through the management default route. Commissioning machines reach the downloaded images through the rack controller on the provisioning VLAN.

## P. First PXE deployment

1. Connect the test machine to a GS724T provisioning port from the worksheet. That port is untagged VLAN 100 with PVID 100.
2. In the machine firmware, enable UEFI network boot and put it first.
3. Power the machine on.
4. On the MAAS server, confirm DHCP is active with `sudo maas status` and that `dhcpd` is running. A machine that never receives an address is a section Q problem, not a reason to enable DHCP on the management VLAN.
5. The machine should appear in MAAS as a newly enlisted machine. Accept or commission it from the web interface.
6. Commission the machine. Commissioning uses the rack controller on the provisioning network.
7. Select the synced Ubuntu image.
8. Deploy Ubuntu.
9. When deployment finishes, the machine's address is the one MAAS assigned from the provisioning range. From the MAAS server:

```bash
ping -I provisioning <deployed-address>
```

The deployed machine can reach the MAAS controller on the provisioning VLAN. It has no default route off that VLAN, because the routing decision is isolation. If the machine later needs the existing LAN or the Internet, move its switch port in the web interface as a separate change. Do not enable NAT on the MAAS server to paper over that.

## Q. Troubleshooting

Incorrect VLAN membership. A machine port left untagged in the management VLAN, or never added to VLAN 100, stays on the existing LAN. Re-open Switching, VLAN, Advanced, VLAN Membership and compare it with section G. Also check that the machine port is not still untagged in VLAN 2 or 3.

Incorrect PVID. The port can be a member of VLAN 100 and still classify incoming untagged frames into the management VLAN. Set the machine-port PVID to the provisioning VLAN ID. Creating the VLAN does not set PVID.

Wrong physical switch port. LED identification is the source of truth. A configured port with no cable does nothing for the machine you plugged in elsewhere. Correct the physical port settings, update any local notes, and repeat switch confirmation and the full acceptance session. Cabling and worksheet changes do not change the YAML hash; software cannot detect them automatically. A YAML change invalidates the previous switch confirmation through its configuration hash.

No VLAN connectivity. On the server, `ip -d link show provisioning` must show the configured VLAN ID, and `ip address show provisioning` must show the controller address. Then check the MAAS port: management untagged, provisioning tagged, management PVID. A tagged server and an untagged machine port is the expected combination. Two tagged ports, or two untagged ports, will not pass the test-host ping.

Lost management connectivity. You likely changed the management VLAN, the router port, or the PVID of the port that reaches your browser. Follow section F rollback. Do not change the router DHCP scope as a workaround.

Unexpected DHCP. The acceptance capture listens on the `provisioning` interface for UDP ports 67 and 68. Any offer before MAAS DHCP is enabled is a `FAIL`. Find the other server before activating MAAS. Do not disable the router's DHCP from this procedure. The router should not be a member of VLAN 100, so its DHCP service should not be heard there. If it is heard, the router port was added to VLAN 100 or a port PVID is wrong.

No PXE response. Confirm `dhcpd` is running, the machine is on a provisioning port, firmware network boot is enabled, and the image import finished. `sudo maas status` and the Images page are the two checks. An incomplete validation must not be overridden to force DHCP on.

Missing routing. A deployed machine that cannot reach the Internet is behaving as designed while it remains on the isolated VLAN. The MAAS controller should still reach the Internet through the management gateway: `ping -I <physical-nic> <management-gateway>`. Do not add a default route on `provisioning`.

Failed image synchronization. The controller needs DNS and a default route on the management LAN, and the first-boot log shows the import poll. Re-run `sudo ansible-playbook install.yml` from `/opt/maas-deploy/ansible` after DNS works. The role skips the download when `/var/lib/maas-provision/images-synced` exists. Remove that marker only if you intend to import again.

PostgreSQL. `systemctl status postgresql` and `sudo -u postgres psql -c '\l'`. The MAAS database is the name in `deployment.postgresql.database`. Ansible does not drop it on a rerun. If `pg_hba.conf` was edited by hand inside the marked MAAS block, the next PostgreSQL role run rewrites that block.

Ansible or cloud-init. `/var/log/cloud-init-output.log`, `/var/log/maas-provision/first-boot.log`, and `/var/log/maas-provision/ansible.log`. If the completion marker is absent, `sudo systemctl start maas-firstboot.service` retries the playbook. If cloud-init never copied the tree, the early-command log on the installer console is the place to look; an incomplete YAML fails before the copy.

## R. Maintenance and disaster recovery

Health. A timer runs `maas-healthcheck` about five minutes after boot and then every 15 minutes. You can run `sudo maas-healthcheck` any time. It checks that PostgreSQL is active, region and rack are running, DHCP matches the marker file, disk use on `/` is under 80 percent, and the secrets file is mode `0600`. The check queries MAAS for the actual VLAN configuration, so a correctly active provisioning DHCP does not cause an automatic management-DHCP failure. It still does not replace a fresh physical isolation test.

Backups. A timer runs `maas-backup` daily at 02:30 with up to 30 minutes of delay. The dump is a PostgreSQL custom-format file, `/var/backups/maas/maas-TIMESTAMP.dump`, mode `0600`. Each completed dump has a mode-0600 `.config.tar.gz` companion containing effective configuration and credentials. The newest completed dump/bundle pairs are kept, up to `deployment.backup.retain`. If `offhost_target` is set, each new dump is also sent with `rsync`. Put the off-host copy on a different machine. A dump that exists only on the MAAS disk is lost with the disk.

Run a backup by hand:

```bash
sudo maas-backup
```

Restore requires an initialized controller with compatible credentials and MAAS migrations. It verifies the archive in a separate database before stopping MAAS, then swaps database names and checks API/service readiness. The original database remains as a rollback copy; a failed availability check restores its name:

```bash
sudo maas-restore --confirm RESTORE /var/backups/maas/maas-TIMESTAMP.dump
```

Real PostgreSQL 16 integration tests cover successful restore, invalid archive rejection, schema mismatch and rollback on failed readiness. MAAS stop/start is simulated in that database test; physical disaster recovery still requires a rehearsal. Take a fresh dump first.

MAAS configuration backup is the PostgreSQL dump. The YAML in `/opt/maas-deploy/config` is the source for Netplan and for the network plan. Keep a copy of the finished YAML with the off-host backups. The secrets file is included in the protected companion configuration bundle, not the SQL dump. Copy both files off the server. Neither alone is a complete snap/hardware recovery image.

Logging. First-boot and Ansible logs are under `/var/log/maas-provision/`. Service logs are `journalctl -u postgresql`, `journalctl -u maas-firstboot.service`, and `journalctl -u snap.maas.pebble.service`.

Storage. `sudo maas-healthcheck` includes disk percentage. Images and PostgreSQL both live on the system disk. Expand or archive before 80 percent.

Security updates. Unattended upgrades install the Ubuntu security pocket and do not reboot automatically. Reboot the controller only when you can accept a DHCP and PXE outage.

MAAS upgrades. Snap refresh for `maas` is held. The channel pin is `3.7/stable`. Do not run `snap refresh` as an unattended major upgrade. Read the Canonical MAAS release notes, take a database dump, and refresh to a channel you have tested. If the installed channel is already different, Ansible refuses to change it.

PostgreSQL. Stay on major version 16 unless you have a tested dump and restore plan. Do not let an Ubuntu release upgrade move the cluster as a side effect. A major PostgreSQL upgrade means dump, install, restore, and a MAAS check, in that order.

Re-running Ansible. Section L. Safe to repeat. It will not rotate passwords or drop the database.

DHCP emergency shutdown. Section N, `sudo maas-network-deactivate --emergency`.

VLAN troubleshooting and switch rollback. Sections Q and F.

MAAS server failure. Machines already installed keep their disks. New enlistment and deployment stop, and so does MAAS DHCP. Rebuild Ubuntu with this ISO onto a replacement server, or onto the same server after disk replacement, using a disk identity that matches the new drive. Restore `/etc/maas-provision/secrets.env` before the playbook creates a new password. Restore the PostgreSQL dump after MAAS packages are present, or let Ansible create an empty database and then restore over it with `maas-restore`. Use the same provisioning address so existing DHCP configuration and switch ports still match. Then run section M again before turning DHCP on.

Complete disaster recovery is: switch configuration export, finished YAML, secrets file, PostgreSQL dumps off the server, and the Ubuntu ISO checksum. Losing the secrets file means the database password in PostgreSQL no longer matches what Ansible expects to read. Recover the role password from the PostgreSQL cluster with a local peer connection (`sudo -u postgres psql`) and write it back into the secrets file. Do not drop the role to generate a new password.

The single controller remains a single point of failure after all of the above. Backups reduce data loss. They do not keep PXE running while the server is down.

## Repository layout

```text
.github/workflows/build-iso.yml
autoinstall/autoinstall.yaml.j2
autoinstall/meta-data
ansible/install.yml
ansible/roles/system
ansible/roles/network
ansible/roles/security
ansible/roles/postgresql
ansible/roles/maas
ansible/roles/maas-network
ansible/roles/maas-images
ansible/roles/backup
ansible/roles/healthcheck
config/network.yaml
config/deployment.yaml
config/schema/
maas_deploy/
scripts/discover-network.sh
scripts/discover-network.ps1
scripts/validate-config.py
scripts/validate-provisioning-network.sh
scripts/observe-management-lan.sh
scripts/provisioning-test-host.sh
scripts/build-iso.sh
scripts/first-boot.sh
scripts/activate-dhcp.sh
scripts/deactivate-dhcp.sh
scripts/confirm-switch.sh
scripts/healthcheck.sh
scripts/backup-maas.sh
scripts/restore-maas.sh
systemd/
tests/
```

Operator names on the installed server are `maas-network-activate`, `maas-network-deactivate`, `maas-network-validate`, `maas-network-confirm-switch`, `maas-healthcheck`, `maas-backup`, and `maas-restore`.

## What automated tests cover

`pytest` covers the schema, Actions input coverage, VLAN rules, DHCP range safety, subnet overlap, MAC checks, Netplan rendering, autoinstall rendering, disk identity, DHCP refusal when validation is missing or stale, and a QEMU boot of a small GRUB ISO after the same injection path used for Ubuntu. `ansible-playbook --syntax-check` and `ansible-lint` cover the playbook. A local `scripts/build-iso.sh` run builds the real Ubuntu ISO and checks its checksum.

Those tests are static checks and virtual-machine checks. They do not program a GS724T, and they do not show that a physical provisioning VLAN is isolated from the existing LAN. Section M is the acceptance test that remains.

## Network filtering

See [Network security options](docs/network-security.md) for server firewall and GS724Tv4 ACL recommendations. No host firewall is currently installed by this repository; routing is disabled, but that does not filter access to the controller itself.

## Corrective validation

See [review corrections and integration tests](docs/review-fixes.md) for the reviewed defects, their fixes, the revised physical acceptance workflow, and the distinction between unit, PostgreSQL and full-VM validation. The manual **Full ISO installation smoke test** boots the real ISO without installation Secrets; it checks UEFI installation, MAAS, installed commands, backup/database restore, rerun and reboot.
