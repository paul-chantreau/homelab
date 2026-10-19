#!/usr/bin/env python3
"""Create missing repository Actions variables using an authorized GitHub CLI."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

def catalog():
    path = Path(__file__).resolve().parents[1] / 'config/actions-variables.json'
    return json.loads(path.read_text(encoding='utf-8'))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', default='paul-chantreau/homelab')
    args = parser.parse_args()
    endpoint = f'repos/{args.repo}/actions/variables'
    # Read every page before making changes. Existing values are never printed
    # or overwritten, including empty values intentionally saved by the owner.
    result = subprocess.run(['gh', 'api', '--paginate', '--slurp', endpoint], capture_output=True, text=True)
    if result.returncode:
        print('Cannot read repository Actions variables. GitHub Variables read/write permission is required.', file=sys.stderr)
        return 1
    existing = {item['name'] for page in json.loads(result.stdout) for item in page['variables']}
    for name, spec in catalog().items():
        if spec.get('source') == 'secret':
            print(f'{name}: configure in Actions Secrets (not Variables)')
            continue
        if name in existing:
            print(f'{name}: preserved')
            continue
        value = str(spec.get('default', '__REQUIRED__'))
        payload = json.dumps({'name': name, 'value': value})
        result = subprocess.run(['gh', 'api', '--method', 'POST', endpoint, '--input', '-'], input=payload, capture_output=True, text=True)
        if result.returncode:
            print(f'{name}: creation failed; stopped without changing existing variables.', file=sys.stderr)
            return 1
        print(f'{name}: created ({"default" if "default" in spec else "placeholder; fill in GitHub"})')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
