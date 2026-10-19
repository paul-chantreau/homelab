#!/usr/bin/env python3
"""Check Actions variables, optionally write the validated ISO configuration."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from maas_deploy.actions_config import resolve
from maas_deploy.config import ConfigError, load_config


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--write-config', action='store_true')
    args = parser.parse_args()
    try:
        variables = json.loads(os.environ.get('ACTIONS_VARIABLES_JSON', '{}'))
        if not isinstance(variables, dict):
            raise ValueError('Variables must be a JSON object')
        config, statuses = resolve(load_config(args.root / 'config/network.yaml', args.root / 'config/deployment.yaml'), variables)
    except (ConfigError, ValueError) as error:
        # Diagnostics identify fields and expected types, never supplied values.
        print(str(error), file=sys.stderr)
        return 1
    summary = '\n'.join(['## Actions configuration', '', '| Variable | Source |', '| --- | --- |'] +
                        [f'| `{name}` | {status} |' for name, status in statuses.items()]) + '\n'
    print(summary)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as handle:
            handle.write(summary)
    if args.write_config:
        for name, value in [('network', config.network), ('deployment', config.deployment)]:
            (args.root / 'config' / f'{name}.yaml').write_text(yaml.safe_dump({name: value}, sort_keys=False), encoding='utf-8')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
