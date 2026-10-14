#!/usr/bin/env python3
"""Validate config/network.yaml and config/deployment.yaml."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from maas_deploy.config import ConfigError, load_config, validate  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--network", type=Path, required=True)
    parser.add_argument("--deployment", type=Path, required=True)
    parser.add_argument("--mode", choices=("schema", "install"), default="schema")
    args = parser.parse_args()
    try:
        validate(load_config(args.network, args.deployment), args.mode)
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 1
    print(f"{args.mode} validation passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
