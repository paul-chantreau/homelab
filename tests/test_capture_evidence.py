"""Packet bytes and failed capture processes must not turn into an empty PASS."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import struct

import pytest

from maas_deploy.capture import capture, dhcp_counts, packets
from maas_deploy.acceptance import evaluate
from test_dhcp_safety import _facts


def pcap(frames):
    data = struct.pack('<IHHIIII', 0xa1b2c3d4, 2, 4, 0, 0, 65535, 1)
    for frame in frames:
        data += struct.pack('<IIII', 0, 0, len(frame), len(frame)) + frame
    return data


def reply(kind):
    dhcp = b'\x02' + b'\0' * 235 + b'\x63\x82\x53\x63' + bytes([53, 1, kind, 255])
    udp = struct.pack('!HHHH', 67, 68, 8 + len(dhcp), 0) + dhcp
    ip = b'\x45\x00' + struct.pack('!H', 20 + len(udp)) + b'\x00\x00\x00\x00\x40\x11\x00\x00' + b'\0' * 8
    return b'\xff' * 6 + b'\x01' * 6 + b'\x08\x00' + ip + udp


def test_dhcp_offer_and_ack_are_decoded_without_text_format_assumptions(tmp_path):
    path = tmp_path / 'dhcp.pcap'
    path.write_bytes(pcap([reply(2), reply(5)]))
    assert dhcp_counts(path) == {'packet_count': 2, 'offer_count': 1, 'server_reply_count': 2}


def test_truncated_capture_is_not_empty_success(tmp_path):
    path = tmp_path / 'bad.pcap'
    path.write_bytes(pcap([reply(2)])[:-1])
    with pytest.raises(ValueError):
        dhcp_counts(path)


def test_immediate_tcpdump_failure_cannot_validate_capture(tmp_path, monkeypatch):
    from types import SimpleNamespace
    class BrokenCapture:
        def wait(self, **kwargs):
            return 1
        def communicate(self):
            return b'', b'permission denied'
    monkeypatch.setattr('maas_deploy.capture.subprocess.Popen', lambda *a, **k: BrokenCapture())
    result = capture('fake0', 30, 'arp', tmp_path / 'missing.pcap')
    assert result['capture_complete'] is False


@pytest.mark.parametrize('defect', ['failed', 'old', 'outside-window', 'no-broadcast'])
def test_inadequate_isolation_evidence_blocks_dhcp(ready, defect):
    facts = _facts(ready)
    if defect == 'failed':
        facts['isolation_report']['capture_complete'] = False
    elif defect == 'old':
        facts['isolation_report']['finished_at'] = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    elif defect == 'outside-window':
        facts['test_host']['probe']['broadcast_started_at'] = datetime.now(timezone.utc).isoformat()
    else:
        facts['test_host']['probe'] = {}
    report = evaluate(ready.network, facts, ready.canonical_hash)
    assert report['blocks_dhcp']
    assert report['checks']['vlan_isolation']['status'] == 'INCONCLUSIVE'


def test_untagged_and_tagged_broadcasts_decode_without_bpf_vlan_offsets():
    from maas_deploy.capture import network_payload
    arp = b'\x00' * 14 + bytes([198, 51, 100, 50]) + b'\x00' * 10
    plain = b'\xff' * 6 + b'\x01' * 6 + b'\x08\x06' + arp
    tagged = plain[:12] + b'\x81\x00\x00\x64' + plain[12:]
    protocol, payload, tags = network_payload(1, plain)
    assert protocol == 0x0806 and payload[14:18] == bytes([198, 51, 100, 50]) and tags == []
    protocol, payload, tags = network_payload(1, tagged)
    assert protocol == 0x0806 and tags == [100]
