"""Short-lived TCP listener used by the provisioning acceptance test."""

from __future__ import annotations

import argparse
import socket
import sys
from pathlib import Path


def serve(address: str, port: int, seconds: int) -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((address, port))
        server.listen(1)
        server.settimeout(seconds)
        try:
            connection, _peer = server.accept()
        except TimeoutError:
            return ""
        except socket.timeout:
            return ""
        with connection:
            connection.settimeout(5)
            data = connection.recv(256)
    return data.decode("utf-8", errors="replace").strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--address", required=True)
    parser.add_argument("--port", type=int, default=19999)
    parser.add_argument("--seconds", type=int, default=45)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    payload = serve(args.address, args.port, args.seconds)
    args.output.write_text(payload + ("\n" if payload else ""), encoding="utf-8")
    return 0 if payload.startswith("PROVISIONING-TEST-OK") else 1


if __name__ == "__main__":
    sys.exit(main())
