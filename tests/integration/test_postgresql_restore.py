"""Actual PostgreSQL 16 dump/restore; opt in with HOMELAB_TEST_POSTGRES=1."""
import os
from pathlib import Path
import subprocess
import uuid

import pytest

from maas_deploy.backup import create_backup, restore_database

pytestmark = pytest.mark.integration


@pytest.fixture(scope='module')
def postgres():
    if os.environ.get('HOMELAB_TEST_POSTGRES') != '1':
        pytest.skip('set HOMELAB_TEST_POSTGRES=1 to start an isolated PostgreSQL 16 container')
    name = 'homelab-pg-test-' + uuid.uuid4().hex[:10]
    subprocess.run(['docker', 'run', '-d', '--rm', '--network', 'none', '--name', name,
                    '-e', 'POSTGRES_HOST_AUTH_METHOD=trust', 'postgres:16'], check=True, capture_output=True)
    prefix = ['docker', 'exec', '-i', name, 'runuser', '-u', 'postgres', '--']
    import time
    try:
        for _ in range(60):
            # The image's temporary initialization server accepts socket
            # connections before being restarted. Only the final server
            # listens on TCP, so wait for that one before creating databases.
            if subprocess.run([*prefix, 'pg_isready', '-h', '127.0.0.1'], capture_output=True).returncode == 0:
                break
            time.sleep(1)
        else:
            pytest.fail('PostgreSQL container did not become ready')
        yield prefix
    finally:
        subprocess.run(['docker', 'rm', '-f', name], check=False, capture_output=True)


def psql(prefix, db, statement):
    return subprocess.run([*prefix, 'psql', '-v', 'ON_ERROR_STOP=1', '-d', db, '-tAc', statement],
                          check=True, text=True, capture_output=True).stdout.strip()


@pytest.fixture
def database(postgres):
    name = 'maas_' + uuid.uuid4().hex[:10]
    psql(postgres, 'postgres', f'CREATE DATABASE {name};')
    psql(postgres, name, "CREATE TABLE django_migrations (app text, name text); INSERT INTO django_migrations VALUES ('maas','001'); CREATE TABLE maasserver_node (hostname text); INSERT INTO maasserver_node VALUES ('saved-node');")
    return name


def snap_stub(command, **kwargs):
    if command[:1] == ['snap']:
        return subprocess.CompletedProcess(command, 0)
    return subprocess.run(command, **kwargs)


def test_private_archive_can_be_restored_without_postgres_opening_root_paths(tmp_path, postgres, database):
    tmp_path.chmod(0o700)
    archive = tmp_path / 'maas.dump'
    create_backup(database, archive, prefix=postgres)
    assert archive.stat().st_mode & 0o777 == 0o600
    psql(postgres, database, "UPDATE maasserver_node SET hostname='newer-node';")
    previous = restore_database(database, 'postgres', archive, prefix=postgres, run=snap_stub)
    assert psql(postgres, database, 'SELECT hostname FROM maasserver_node') == 'saved-node'
    assert psql(postgres, previous, 'SELECT hostname FROM maasserver_node') == 'newer-node'


def test_broken_archive_cannot_replace_existing_database(tmp_path, postgres, database):
    archive = tmp_path / 'broken.dump'
    archive.write_bytes(b'not a PostgreSQL archive')
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        return snap_stub(command, **kwargs)
    with pytest.raises(subprocess.CalledProcessError):
        restore_database(database, 'postgres', archive, prefix=postgres, run=run)
    assert not any(command[0] == 'snap' for command in calls)
    assert psql(postgres, database, 'SELECT hostname FROM maasserver_node') == 'saved-node'


def test_failed_readiness_rolls_back_to_original_database(tmp_path, postgres, database):
    archive = tmp_path / 'maas.dump'
    create_backup(database, archive, prefix=postgres)
    psql(postgres, database, "UPDATE maasserver_node SET hostname='original-live';")
    def fail():
        raise RuntimeError('MAAS health check failed')
    with pytest.raises(RuntimeError):
        restore_database(database, 'postgres', archive, prefix=postgres, run=snap_stub, readiness=fail)
    assert psql(postgres, database, 'SELECT hostname FROM maasserver_node') == 'original-live'


def test_incompatible_migrations_are_rejected_before_service_stop(tmp_path, postgres, database):
    archive = tmp_path / 'maas.dump'
    create_backup(database, archive, prefix=postgres)
    psql(postgres, database, "INSERT INTO django_migrations VALUES ('maas','002');")
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        return snap_stub(command, **kwargs)
    with pytest.raises(ValueError, match='schema version'):
        restore_database(database, 'postgres', archive, prefix=postgres, run=run)
    assert not any(command[0] == 'snap' for command in calls)
    assert psql(postgres, database, 'SELECT count(*) FROM django_migrations') == '2'
