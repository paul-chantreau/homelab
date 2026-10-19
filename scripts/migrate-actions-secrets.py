#!/usr/bin/env python3
"""Move existing topology Variables to Secrets without printing their values."""
import argparse
import json
from pathlib import Path
import subprocess
import sys


def api_pages(endpoint):
    result = subprocess.run(['gh', 'api', '--paginate', '--slurp', endpoint], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError('GitHub access denied; Variables and Secrets read/write permissions are required.')
    return json.loads(result.stdout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', default='paul-chantreau/homelab')
    args = parser.parse_args()
    endpoint = f'repos/{args.repo}/actions'
    try:
        variables = {item['name']: item['value'] for page in api_pages(endpoint + '/variables') for item in page['variables']}
        secrets = {item['name'] for page in api_pages(endpoint + '/secrets') for item in page['secrets']}
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        return 1
    catalog = json.loads((Path(__file__).resolve().parents[1] / 'config/actions-variables.json').read_text())
    for name, spec in catalog.items():
        if spec['source'] != 'secret':
            continue
        if name in secrets:
            # Secret values cannot be read back. Never overwrite an existing
            # secret or remove a duplicate Variable whose equivalence is unknown.
            print(f'{name}: Secret exists' + ('; duplicate Variable requires manual review/removal' if name in variables else ''))
            continue
        value = variables.get(name, '')
        if not value.strip() or value.strip() == '__REQUIRED__':
            print(f'{name}: no usable Variable to migrate; configure Secret manually if required')
            continue
        result = subprocess.run(['gh', 'secret', 'set', name, '--repo', args.repo], input=value, capture_output=True, text=True)
        if result.returncode:
            print(f'{name}: Secret creation failed; source Variable preserved.', file=sys.stderr)
            return 1
        # Confirm name visibility before removing the source. Never retrieve a
        # secret value, write values to temporary files, or place them in argv.
        try:
            names = {item['name'] for page in api_pages(endpoint + '/secrets') for item in page['secrets']}
        except RuntimeError:
            print(f'{name}: cannot confirm Secret metadata; source Variable preserved.', file=sys.stderr)
            return 1
        if name not in names:
            print(f'{name}: Secret creation unconfirmed; source Variable preserved.', file=sys.stderr)
            return 1
        result = subprocess.run(['gh', 'api', '--method', 'DELETE', endpoint + '/variables/' + name], capture_output=True, text=True)
        if result.returncode:
            print(f'{name}: Secret created; source Variable deletion failed.', file=sys.stderr)
            return 1
        print(f'{name}: migrated to Secrets; source Variable removed')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
