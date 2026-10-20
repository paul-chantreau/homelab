# Network filtering options for this homelab

## Current implementation

The system role disables IPv4 and IPv6 forwarding. The SSH role disables root and password authentication. The design isolates provisioning in a VLAN and requires physical isolation acceptance before DHCP activation. There is currently no UFW, nftables or iptables ruleset installed by this repository. VLAN membership and switch management settings are configured manually.

Keeping topology in GitHub Secrets limits disclosure. It does not enforce network isolation, close ports or encrypt the installation ISO.

## Recommended: nftables on the MAAS server

Ubuntu 24.04 supports nftables. A single managed ruleset is suitable for interface-based IPv4/IPv6 filtering and connection tracking. Use Ansible to install and persist it, validate with `nft -c -f`, then activate with a scheduled rollback and console access. Do not combine independent UFW/nftables managers or flush an existing ruleset blindly.

Proposed policy, to be implemented and tested separately:

| Traffic | Proposed policy |
| --- | --- |
| Loopback and established/related replies | Allow |
| New SSH connections | Allow only from explicit administrator addresses/subnets on management |
| MAAS administrative web/API | Allow only from administrator addresses or a VPN; avoid publishing it to the WAN |
| Provisioning clients to MAAS | Allow the MAAS/PXE/DHCP/DNS/TFTP/time/image/metadata services actually used, only on the provisioning interface and with suitable source rules |
| PostgreSQL | Local access only; verify listen addresses as well as firewall rules |
| IPv4/IPv6 forwarding | Deny; preserve disabled routing and no NAT |
| Other inbound traffic | Deny, with rate-limited diagnostic logging |
| Outbound traffic initially | Allow for installation, updates and image synchronization; restrict later only after destinations/protocols are documented |
| ICMP/ICMPv6 | Permit the messages required for diagnostics, neighbour discovery and path MTU; do not blanket-block IPv6 control traffic |

Provisioning cannot be restricted to already assigned client IPs: initial DHCP and PXE include broadcasts and clients without addresses. TFTP uses dynamically negotiated UDP flows; allowing only UDP destination port 69 can break transfers. Determine the complete port/flow requirements for the installed MAAS 3.7 roles, including rack/region communication, instead of applying a generic web-server ruleset.

A host firewall protects MAAS. It does not inspect direct traffic between two other machines on the same VLAN and does not repair incorrect switch VLAN membership. Do not install Docker/Portainer on this controller without reviewing the network changes it introduces.

## Simpler alternative: UFW

UFW is easier for a small setup: deny incoming/routed traffic, allow administrator SSH, and add provisioning interface rules. It still needs the same MAAS service inventory, broadcast/TFTP testing, IPv6 handling and rollback. Use it instead of a separately managed nftables ruleset, not alongside one.

## Complement: GS724Tv4 VLAN configuration and ACLs

Keep the router-facing port out of provisioning, preserve management untagged and provisioning tagged on the MAAS trunk, and keep machine ports untagged with the provisioning PVID. Inspect membership in predefined VLANs as well as the chosen provisioning VLAN.

Use ACL capabilities documented in the GS724Tv4 administration manual to restrict access to switch management and unwanted flows between ports/VLANs where supported. Confirm the ACL type, direction, binding, rule order and DHCP/broadcast behaviour before applying it. Do not assume a switch ACL is a stateful firewall or that an IP ACL alone filters every Layer-2 path. Avoid changing the administrative access path without a rollback plan and a saved switch configuration.

Reference: the [GS724Tv4 administration manual](https://www.downloads.netgear.com/files/GDC/GS716TV3/GS716Tv3_GS724Tv4_GS748Tv5_SWA_25Sept2013.pdf), matching the fixed GS724Tv4 switch model assumed by this deployment.

## If inter-VLAN routing is needed later

A dedicated OPNsense/pfSense firewall, or a router with suitable VLAN and firewall support, can enforce stateful policies and terminate WireGuard/VPN. It must actually be in the traffic path. This would change the current isolated design and requires a separate routing plan; putting firewall software in a VM that traffic bypasses offers no protection.

## Acceptance before enabling filtering permanently

Record administrator source addresses and console recovery, capture existing network/rules, and test the candidate with automatic rollback. Verify an administrator can open a new SSH session, unauthorized sources cannot reach management, and DHCP/PXE/image download/commissioning/deployment still work. Test after reboot and rerun the physical isolation acceptance. A firewall must not make an untested claim that the switch VLAN is isolated.

No firewall rules have been activated or tested against a live MAAS deployment during this change.
