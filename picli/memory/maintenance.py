"""Online SQLite snapshots and conservative shared-artifact garbage collection.

Usage: python -m picli.storage_maintenance --workspace PATH backup DESTINATION
       python -m picli.storage_maintenance --workspace PATH gc [--apply]
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile

from picli.memory.storage_lock import storage_lock


DIGEST = re.compile(r'[0-9a-f]{64}')


def quote(identifier):
    return '"' + identifier.replace('"', '""') + '"'


def references(directory):
    """Scan every JSON text column in current and legacy databases."""
    found = set()
    databases = sorted(directory.glob('*.sqlite3'))
    for path in databases:
        db = sqlite3.connect(f'{path.as_uri()}?mode=ro', uri=True)
        try:
            if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError(f'Database integrity check failed: {path.name}')
            tables = db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
            for (table,) in tables:
                columns = [row[1] for row in db.execute(f'PRAGMA table_info({quote(table)})')
                           if row[2].upper() in {'TEXT', 'JSON'}]
                for column in columns:
                    for (text,) in db.execute(f'SELECT {quote(column)} FROM {quote(table)}'):
                        if not isinstance(text, str) or 'picli-artifacts-v1' not in text:
                            continue
                        try:
                            data = json.loads(text)
                        except ValueError:
                            raise ValueError(f'Malformed artifact payload in {path.name}/{table}')
                        if not isinstance(data, dict) or data.get('payload_format') != 'picli-artifacts-v1':
                            continue
                        for ref in data['references']:
                            digest = ref['sha256']
                            if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
                                raise ValueError('Invalid artifact reference; maintenance aborted')
                            found.add(digest)
        finally:
            db.close()
    return databases, found


def validate_artifact(path, digest):
    if path.is_symlink() or not path.is_file():
        raise ValueError(f'Missing or unsafe artifact: {digest}')
    hasher = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            hasher.update(chunk)
    if hasher.hexdigest() != digest:
        raise ValueError(f'Artifact integrity check failed: {digest}')


def collect(workspace, *, apply=False):
    directory = workspace.resolve() / '.pi-python'
    with storage_lock(directory, exclusive=True):
        databases, live = references(directory)
        artifacts = directory / 'artifacts'
        for digest in live:
            validate_artifact(artifacts / digest, digest)
        candidates = [p for p in artifacts.glob('*')
                      if DIGEST.fullmatch(p.name) and p.is_file() and not p.is_symlink() and p.name not in live]
        reclaimed = sum(p.stat().st_size for p in candidates)
        if apply:
            for path in candidates:
                path.unlink()
        return dict(applied=apply, databases=len(databases), live=len(live),
                    orphan_files=len(candidates), bytes=reclaimed)


def backup(workspace, destination):
    directory = workspace.resolve() / '.pi-python'
    destination = destination.expanduser().resolve()
    if destination == directory or directory in destination.parents:
        raise ValueError('Backup destination must be outside the live storage directory')
    if destination.exists():
        raise ValueError('Backup destination already exists')
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.picli-backup-', dir=destination.parent))
    try:
        with storage_lock(directory, exclusive=True):
            databases, live = references(directory)
            target = staging / '.pi-python'
            target.mkdir()
            for path in databases:
                source = sqlite3.connect(f'{path.as_uri()}?mode=ro', uri=True)
                dest = sqlite3.connect(target / path.name)
                try:
                    source.backup(dest)
                finally:
                    dest.close()
                    source.close()
            (target / 'artifacts').mkdir()
            for digest in live:
                path = directory / 'artifacts' / digest
                validate_artifact(path, digest)
                shutil.copyfile(path, target / 'artifacts' / digest)
            (staging / 'manifest.json').write_text(json.dumps(dict(
                format='picli-storage-backup-v1', databases=[p.name for p in databases],
                artifacts=sorted(live)), indent=2), encoding='utf-8')
            # Publish only after all database snapshots and referenced files exist.
            if destination.exists():
                raise ValueError('Backup destination already exists')
            staging.rename(destination)
        return dict(destination=str(destination), databases=len(databases), artifacts=len(live))
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, default=Path.cwd())
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('gc').add_argument('--apply', action='store_true')
    commands.add_parser('backup').add_argument('destination', type=Path)
    args = parser.parse_args()
    try:
        result = collect(args.workspace, apply=args.apply) if args.command == 'gc' else backup(args.workspace, args.destination)
    except (OSError, ValueError, sqlite3.Error) as error:
        parser.exit(1, f'Storage maintenance failed: {error}\n')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
