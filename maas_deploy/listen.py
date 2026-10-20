"""Correlate a fresh broadcast probe with a connection from the test host."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import socket


def serve(address: str, port: int, seconds: int, token: str, expected_host: str) -> dict:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((address, port))
        server.listen(1)
        server.settimeout(seconds)
        try:
            connection, peer = server.accept()
            with connection:
                connection.settimeout(5)
                data = bytearray()
                while b'\n' not in data and len(data) < 4096:
                    block = connection.recv(1024)
                    if not block:
                        break
                    data.extend(block)
            probe = json.loads(data)
            if peer[0] != expected_host or probe.get('token') != token or probe.get('broadcast_sent') is not True:
                return {}
            start = datetime.fromisoformat(probe['broadcast_started_at'])
            end = datetime.fromisoformat(probe['broadcast_finished_at'])
            now = datetime.now(timezone.utc)
            if start.tzinfo is None or end.tzinfo is None or not 0 <= (now - start).total_seconds() <= seconds:
                return {}
            if not start <= end <= now:
                return {}
            return {**probe, 'verified_broadcast': True, 'peer_address': peer[0], 'received_at': now.isoformat()}
        except (OSError, ValueError, KeyError, TypeError):
            return {}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--address', required=True)
    parser.add_argument('--port', type=int, default=19999)
    parser.add_argument('--seconds', type=int, default=45)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--token', required=True)
    parser.add_argument('--expected-host', required=True)
    args = parser.parse_args(argv)
    payload = serve(args.address, args.port, args.seconds, args.token, args.expected_host)
    args.output.write_text(json.dumps(payload) + '\n')
    return 0 if payload else 1


if __name__ == '__main__':
    raise SystemExit(main())
