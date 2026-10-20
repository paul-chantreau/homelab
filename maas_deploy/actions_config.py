"""Resolve GitHub Actions variables into the canonical installation config."""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

from maas_deploy.config import ConfigError, INSTALL_MODE, LoadedConfig, validate

CATALOG = Path(__file__).resolve().parents[1] / 'config' / 'actions-variables.json'


def catalog() -> dict[str, Any]:
    return json.loads(CATALOG.read_text(encoding='utf-8'))


def _parse(raw: str, kind: str) -> Any:
    if kind == 'integer':
        if not re.fullmatch(r'[0-9]+', raw):
            raise ValueError('expected an integer')
        return int(raw)
    if kind.endswith('-list'):
        value = json.loads(raw)
        expected = int if kind == 'integer-list' else str
        if not isinstance(value, list) or not value or any(type(item) is not expected for item in value):
            raise ValueError(f'expected a nonempty JSON {kind}')
        return value
    if kind == 'keys':
        return [line.strip() for line in raw.splitlines() if line.strip()]
    return raw


def resolve(base: LoadedConfig, variables: dict[str, Any], secrets: dict[str, Any] | None = None) -> tuple[LoadedConfig, dict[str, str]]:
    secrets = secrets or {}
    document = {'network': copy.deepcopy(base.network), 'deployment': copy.deepcopy(base.deployment)}
    errors = []
    statuses = {}
    for name, spec in catalog().items():
        source = spec.get('source', 'variable')
        values = secrets if source == 'secret' else variables
        raw = values.get(name, '')
        # GitHub expressions can serialize an unset secret as JSON null.
        # Treat it as absent so optional disk alternatives remain optional.
        if raw is None:
            raw = ''
        if not isinstance(raw, str):
            errors.append(f'{name}: expected a string variable')
            continue
        raw = raw.strip()
        if raw == '__REQUIRED__':
            raw = ''
        if raw:
            try:
                value = _parse(raw, spec['type'])
            except (ValueError, TypeError):
                errors.append(f'{name}: invalid {spec["type"]}')
                continue
            statuses[name] = 'configured'
        elif 'default' in spec:
            value = spec['default']
            statuses[name] = 'default'
        elif spec.get('required'):
            errors.append(f'{name}: required {source} is missing or empty')
            continue
        else:
            value = None
            statuses[name] = 'not set (disk alternative)'
        target = document
        *parents, key = spec['path'].split('.')
        for parent in parents:
            target = target[parent]
        target[key] = value
    if not any(isinstance(secrets.get(name), str) and secrets[name].strip() not in ('', '__REQUIRED__')
               for name in ('DISK_SERIAL', 'DISK_WWN', 'DISK_BY_ID')):
        errors.append('At least one Secret DISK_SERIAL, DISK_WWN, DISK_BY_ID is required')
    if errors:
        raise ConfigError(errors)
    # These are deliberately not user-facing Actions variables. A manual ISO
    # build authorizes installation on the specified disk; preinstall-check
    # still requires exactly one matching physical disk before partitioning.
    document['deployment']['disk'].update(model=None, confirm_erase=True)
    document['deployment']['backup']['offhost_target'] = None
    config = LoadedConfig(**document)
    validate(config, INSTALL_MODE)
    return config, statuses
