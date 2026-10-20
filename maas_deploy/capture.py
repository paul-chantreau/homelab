"""Capture evidence with verified duration and decode DHCP from packet bytes."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import ipaddress
import json
import os
import re
from pathlib import Path
import signal
import socket
import struct
import subprocess
import tempfile
import time

from maas_deploy.files import atomic_write


def packets(path):
    """Read classic pcap (Ethernet, Linux cooked v1/v2, or raw IPv4)."""
    with Path(path).open('rb') as handle:
        header = handle.read(24)
        if len(header) != 24:
            raise ValueError('missing pcap header')
        magic = header[:4]
        if magic in (b'\xd4\xc3\xb2\xa1', b'\x4d\x3c\xb2\xa1'):
            endian = '<'
        elif magic in (b'\xa1\xb2\xc3\xd4', b'\xa1\xb2\x3c\x4d'):
            endian = '>'
        else:
            raise ValueError('unsupported pcap format')
        link = struct.unpack(endian + 'I', header[20:24])[0]
        if link not in {1, 101, 113, 276}:
            raise ValueError('unsupported capture link type')
        while True:
            record = handle.read(16)
            if not record:
                return
            if len(record) != 16:
                raise ValueError('truncated pcap record')
            _, _, size, original = struct.unpack(endian + 'IIII', record)
            if size != original or size > 1024 * 1024:
                raise ValueError('truncated or excessive packet')
            data = handle.read(size)
            if len(data) != size:
                raise ValueError('incomplete packet')
            yield link, data


def network_payload(link, frame):
    tags = []
    if link == 1:
        if len(frame) < 14:
            raise ValueError('short Ethernet frame')
        protocol = int.from_bytes(frame[12:14], 'big')
        frame = frame[14:]
        while protocol in {0x8100, 0x88a8}:
            if len(frame) < 4:
                raise ValueError('short VLAN frame')
            tags.append(int.from_bytes(frame[:2], 'big') & 0x0fff)
            protocol = int.from_bytes(frame[2:4], 'big')
            frame = frame[4:]
    elif link == 113:
        protocol = int.from_bytes(frame[14:16], 'big')
        frame = frame[16:]
    elif link == 276:
        protocol = int.from_bytes(frame[:2], 'big')
        frame = frame[20:]
    else:
        protocol = 0x0800
    return protocol, frame, tags


def dhcp_counts(path):
    offers = replies = count = 0
    for link, frame in packets(path):
        count += 1
        protocol, frame, _ = network_payload(link, frame)
        if protocol != 0x0800:
            continue
        if len(frame) < 20 or frame[0] >> 4 != 4:
            raise ValueError('invalid IPv4 packet')
        length = (frame[0] & 15) * 4
        if length < 20 or len(frame) < length + 8:
            raise ValueError('truncated IPv4/UDP header')
        if frame[9] != 17:
            continue
        if int.from_bytes(frame[6:8], 'big') & 0x3fff:
            raise ValueError('fragmented DHCP observation requires reassembly')
        udp = frame[length:]
        source, destination, udp_size, _ = struct.unpack('!HHHH', udp[:8])
        if udp_size > len(udp) or udp_size < 8:
            raise ValueError('truncated UDP payload')
        if source != 67 or destination != 68:
            continue
        bootp = udp[8:udp_size]
        if not bootp:
            raise ValueError('empty BOOTP reply')
        # Any server BOOTP/DHCP reply is unexpected before activation.
        if bootp[0] == 2:
            replies += 1
        if len(bootp) < 240 or bootp[236:240] != b'\x63\x82\x53\x63':
            continue
        options = bootp[240:]
        i = 0
        while i < len(options):
            code = options[i]
            i += 1
            if code == 255:
                break
            if code == 0:
                continue
            if i >= len(options) or i + 1 + options[i] > len(options):
                raise ValueError('truncated DHCP option')
            size = options[i]
            value = options[i + 1:i + 1 + size]
            i += size + 1
            if code == 53 and value == b'\x02':
                offers += 1
    return {'packet_count': count, 'offer_count': offers, 'server_reply_count': replies}


def capture(interface, seconds, expression, path):
    started = datetime.now(timezone.utc)
    begin = time.monotonic()
    command = ['tcpdump', '-U', '-n', '-i', interface, '-s', '0', '-w', str(path)]
    if expression:
        command.append(expression)
    complete = False
    code = None
    dropped = None
    try:
        process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            code = process.wait(timeout=seconds)
        except subprocess.TimeoutExpired:
            process.send_signal(signal.SIGINT)
            try:
                code = process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            complete = code in {0, -signal.SIGINT, 130}
        finally:
            _, diagnostics = process.communicate()
            match = re.search(rb"(\d+) packets dropped by kernel", diagnostics or b"")
            if match:
                dropped = int(match[1])
    except OSError:
        pass
    elapsed = time.monotonic() - begin
    return {'capture_complete': complete and elapsed >= seconds - 0.5 and dropped == 0,
            'packets_dropped': dropped,
            'seconds': elapsed, 'capture_exit': code,
            'started_at': started.isoformat(), 'finished_at': datetime.now(timezone.utc).isoformat()}


def observe(args):
    subnet = ipaddress.ip_network(args.management_subnet, strict=True)
    target = str(ipaddress.ip_address(args.host))
    if not 20 <= args.seconds <= 120:
        raise ValueError('observation must last 20 to 120 seconds')
    links = json.loads(subprocess.check_output(['ip', '-j', 'link'], text=True))
    if any(str(item.get('address', '')).lower() == args.maas_mac.lower() for item in links):
        raise ValueError('observer must not be the MAAS controller')
    addresses = json.loads(subprocess.check_output(['ip', '-j', 'address', 'show', 'dev', args.interface], text=True))
    if not any(ipaddress.ip_address(a['local']) in subnet for item in addresses for a in item.get('addr_info', []) if a.get('family') == 'inet'):
        raise ValueError('observer interface is not on the configured management network')
    with tempfile.TemporaryDirectory() as temp:
        pcap = Path(temp) / 'observe.pcap'
        result = capture(args.interface, args.seconds, '', pcap)
        try:
            count = 0
            for link, frame in packets(pcap):
                protocol, payload, tags = network_payload(link, frame)
                addresses = []
                if protocol == 0x0800 and len(payload) >= 20:
                    addresses = [socket.inet_ntoa(payload[12:16]), socket.inet_ntoa(payload[16:20])]
                elif protocol == 0x0806 and len(payload) >= 28:
                    addresses = [socket.inet_ntoa(payload[14:18]), socket.inet_ntoa(payload[24:28])]
                if args.vlan in tags or target in addresses:
                    count += 1
        except (ValueError, OSError):
            result['capture_complete'] = False
            count = None
    result.update(observer='management-lan', interface=args.interface,
                  captured_on_maas_server=False, management_subnet=str(subnet),
                  vlan_id=args.vlan, observed_host=target, packet_count=count)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for command in ['observe', 'dhcp']:
        p = commands.add_parser(command)
        p.add_argument('--interface', required=True)
        p.add_argument('--seconds', type=int, default=30 if command == 'observe' else 15)
        p.add_argument('--output', type=Path, required=True)
        if command == 'observe':
            p.add_argument('--host', required=True)
            p.add_argument('--vlan', type=int, required=True)
            p.add_argument('--maas-mac', required=True)
            p.add_argument('--management-subnet', required=True)
    args = parser.parse_args(argv)
    if os.geteuid() != 0:
        parser.error('root is required for packet capture')
    if args.command == 'observe':
        result = observe(args)
    else:
        if not 10 <= args.seconds <= 60:
            parser.error('DHCP capture must last 10 to 60 seconds')
        with tempfile.TemporaryDirectory() as temp:
            pcap = Path(temp) / 'dhcp.pcap'
            result = capture(args.interface, args.seconds, 'udp port 67 or udp port 68', pcap)
            try:
                result.update(dhcp_counts(pcap))
            except (ValueError, OSError):
                result['capture_complete'] = False
        result['ran'] = result['capture_complete']
    atomic_write(args.output, json.dumps(result, indent=2) + '\n')
    print(f'Capture complete: {result["capture_complete"]}; report: {args.output}')
    return 0 if result['capture_complete'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
