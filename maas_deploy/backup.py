"""Private PostgreSQL backups and staged, reversible MAAS database restores."""
from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess
import uuid


def select_expired(paths: list[Path], retain: int) -> list[Path]:
    if retain < 1:
        raise ValueError('retain must be positive')
    return sorted(paths, key=lambda path: path.name)[:-retain]


def rsync_command(source: Path, target: str) -> list[str]:
    if not target or target.startswith('-') or ' ' in target:
        raise ValueError('invalid rsync destination')
    return ['rsync', '-a', '--', str(source), target]


def _identifier(name):
    return '"' + name.replace('"', '""') + '"'


def create_backup(database: str, destination: Path, *, run=None, prefix=None) -> None:
    """Root opens the output; pg_dump inherits a writable FD, not root's path."""
    run = run or subprocess.run
    prefix = ['runuser', '-u', 'postgres', '--'] if prefix is None else prefix
    temporary = destination.with_name(f'.{destination.name}.{uuid.uuid4().hex}.partial')
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as handle:
            run([*prefix, 'pg_dump', '--format=custom', database], stdout=handle, check=True)
            handle.flush()
            os.fsync(handle.fileno())
        # Verify the archive before publishing it as a successful backup.
        with temporary.open('rb') as handle:
            run([*prefix, 'pg_restore', '--list'], stdin=handle, stdout=subprocess.DEVNULL, check=True)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def restore_database(database: str, owner: str, archive: Path, *, run=None, prefix=None, readiness=None) -> str:
    """Restore into a temporary database; retain the original as a rollback copy.

    No current database is dropped. If service validation fails after the swap,
    stop MAAS and restore the original database name before restarting it.
    """
    run = run or subprocess.run
    prefix = ['runuser', '-u', 'postgres', '--'] if prefix is None else prefix
    suffix = uuid.uuid4().hex[:12]
    staging = f'{database[:35]}_restore_{suffix}'
    previous = f'{database[:35]}_before_{suffix}'
    sql = lambda statement, check=True: run([*prefix, 'psql', '-v', 'ON_ERROR_STOP=1', '-d', 'postgres', '-tAc', statement], check=check, capture_output=True, text=True)
    # Open and validate before stopping MAAS or changing database names.
    with archive.open('rb') as handle:
        run([*prefix, 'pg_restore', '--list'], stdin=handle, stdout=subprocess.DEVNULL, check=True)
    sql(f'CREATE DATABASE {_identifier(staging)} OWNER {_identifier(owner)};')
    try:
        with archive.open('rb') as handle:
            run([*prefix, 'pg_restore', '--exit-on-error', '--single-transaction', '--no-owner',
                 '--role', owner, '--dbname', staging], stdin=handle, check=True)
        result = run([*prefix, 'psql', '-v', 'ON_ERROR_STOP=1', '-d', staging, '-tAc',
                      "SELECT to_regclass('django_migrations') IS NOT NULL AND to_regclass('maasserver_node') IS NOT NULL;"],
                     check=True, capture_output=True, text=True)
        if result.stdout.strip() != 't':
            raise ValueError('Archive does not contain the expected MAAS schema; current database unchanged')
        migrations = []
        for name in [database, staging]:
            result = run([*prefix, 'psql', '-v', 'ON_ERROR_STOP=1', '-d', name, '-tAc',
                          "SELECT app || ':' || name FROM django_migrations ORDER BY app, name;"],
                         check=True, capture_output=True, text=True)
            migrations.append(result.stdout)
        if migrations[0] != migrations[1]:
            raise ValueError('Backup schema version differs from installed MAAS; restore with a compatible MAAS version')
    except BaseException:
        sql(f'DROP DATABASE IF EXISTS {_identifier(staging)};', check=False)
        raise
    renamed = swapped = False
    try:
        run(['snap', 'stop', 'maas'], check=True)
        sql(f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='{database}' AND pid <> pg_backend_pid();")
        sql(f'ALTER DATABASE {_identifier(database)} RENAME TO {_identifier(previous)};')
        renamed = True
        sql(f'ALTER DATABASE {_identifier(staging)} RENAME TO {_identifier(database)};')
        swapped = True
        run(['snap', 'start', 'maas'], check=True)
        if readiness:
            readiness()
    except BaseException:
        run(['snap', 'stop', 'maas'], check=False)
        if swapped:
            sql(f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='{database}' AND pid <> pg_backend_pid();")
            sql(f'ALTER DATABASE {_identifier(database)} RENAME TO {_identifier(staging)};')
        if renamed:
            sql(f'ALTER DATABASE {_identifier(previous)} RENAME TO {_identifier(database)};')
        run(['snap', 'start', 'maas'], check=False)
        raise
    return previous


def backup_controller(config, root: Path, *, run=None):
    """Retain only completed dumps; credentials/config copies remain root-only."""
    import tarfile
    run = run or subprocess.run
    settings = config.deployment['backup']
    directory = Path(settings['directory'])
    directory.mkdir(parents=True, exist_ok=True, mode=0o750)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    dump = directory / f'maas-{stamp}.dump'
    bundle = dump.with_suffix('.config.tar.gz')
    pending_dump = dump.with_name(f'.{dump.name}.pending')
    pending_bundle = bundle.with_name(f'.{bundle.name}.pending')
    try:
        create_backup(config.deployment['postgresql']['database'], pending_dump, run=run)
        fd = os.open(pending_bundle, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as output:
            with tarfile.open(fileobj=output, mode='w:gz') as tar:
                for source, name in [(root / 'config/network.yaml', 'network.yaml'),
                                     (root / 'config/deployment.yaml', 'deployment.yaml'),
                                     (Path('/etc/maas-provision/secrets.env'), 'secrets.env')]:
                    tar.add(source, arcname=name, recursive=False)
            output.flush()
            os.fsync(output.fileno())
        # Publish the dump last: its presence is the completed-pair marker.
        os.replace(pending_bundle, bundle)
        os.replace(pending_dump, dump)
    except BaseException:
        bundle.unlink(missing_ok=True)
        raise
    finally:
        pending_dump.unlink(missing_ok=True)
        pending_bundle.unlink(missing_ok=True)
    if settings.get('offhost_target'):
        for path in (dump, bundle):
            run(rsync_command(path, settings['offhost_target']), check=True)
    completed = [path for path in directory.glob('maas-*.dump') if path.with_suffix('.config.tar.gz').is_file()]
    for expired in select_expired(completed, int(settings['retain'])):
        expired.unlink()
        expired.with_suffix('.config.tar.gz').unlink(missing_ok=True)
    return dump
