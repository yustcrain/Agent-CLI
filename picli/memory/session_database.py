"""Transactional append-only storage for CLI sessions."""
from contextlib import contextmanager
import sqlite3

from picli.memory.payload_store import PayloadStore
from picli.memory.session import Session, now
from picli.memory.storage_lock import storage_lock


class SessionConflict(ValueError):
    """Another writer saved or deleted the session since it was loaded."""


class SessionNotFound(ValueError):
    pass


class SessionDatabase:
    def __init__(self, path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.payloads = PayloadStore(path.parent / 'artifacts')
        self._migrate()

    @contextmanager
    def connect(self):
        with storage_lock(self.path.parent):
            db = sqlite3.connect(self.path, timeout=30)
            db.execute('PRAGMA foreign_keys=ON')
            try:
                with db:
                    yield db
            except sqlite3.IntegrityError:
                raise
            except sqlite3.Error as error:
                raise ValueError(f'Session database error: {error}') from error
            finally:
                db.close()

    def _migrate(self):
        # DDL and all old records migrate atomically, including schema version.
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('CREATE TABLE IF NOT EXISTS session_storage_meta (version INTEGER NOT NULL)')
            row = db.execute('SELECT version FROM session_storage_meta').fetchone()
            if row:
                if row[0] != 1:
                    raise ValueError(f'Unsupported session storage version: {row[0]}')
                return
            old = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='sessions'").fetchone()
            if old:
                db.execute('ALTER TABLE sessions RENAME TO legacy_sessions')
            db.execute('''CREATE TABLE sessions (
                id TEXT PRIMARY KEY, title TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                head_id TEXT, revision INTEGER NOT NULL DEFAULT 0)''')
            db.execute('CREATE INDEX sessions_updated ON sessions(updated_at DESC)')
            db.execute('''CREATE TABLE message_nodes (
                session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                id TEXT NOT NULL, parent_id TEXT, position INTEGER NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('user','assistant','tool')),
                created_at TEXT NOT NULL, message TEXT NOT NULL,
                PRIMARY KEY(session_id,id), UNIQUE(session_id,position),
                FOREIGN KEY(session_id,parent_id) REFERENCES message_nodes(session_id,id)
                    DEFERRABLE INITIALLY DEFERRED)''')
            db.execute('''CREATE TABLE compactions (
                session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                id TEXT NOT NULL, through_id TEXT, position INTEGER NOT NULL,
                created_at TEXT NOT NULL, data TEXT NOT NULL,
                PRIMARY KEY(session_id,id), UNIQUE(session_id,position),
                FOREIGN KEY(session_id,through_id) REFERENCES message_nodes(session_id,id)
                    DEFERRABLE INITIALLY DEFERRED)''')
            if old:
                for name, encoded in db.execute('SELECT id,data FROM legacy_sessions').fetchall():
                    data = self.payloads.loads(encoded)
                    self._validate(name, data)
                    self._insert(db, name, data)
                db.execute('DROP TABLE legacy_sessions')
            db.execute('INSERT INTO session_storage_meta VALUES (1)')

    @staticmethod
    def _validate(name, data):
        Session._valid_name(name)
        validator = Session.__new__(Session)
        validator._load_tree(name, data)

    def _append(self, db, name, nodes, compactions, node_offset=0, compact_offset=0):
        for position, node in enumerate(nodes, node_offset):
            Session._valid_message(node['message'])
            db.execute('INSERT INTO message_nodes VALUES (?,?,?,?,?,?,?)',
                       (name, node['id'], node['parent_id'], position,
                        node['message']['role'], node['created_at'], self.payloads.dumps(node['message'])))
        for position, item in enumerate(compactions, compact_offset):
            db.execute('INSERT INTO compactions VALUES (?,?,?,?,?,?)',
                       (name, item['id'], item.get('through_id'), position,
                        item['created_at'], self.payloads.dumps(item)))

    def _insert(self, db, name, data):
        timestamp = now()
        db.execute('INSERT INTO sessions(id,created_at,updated_at,head_id) VALUES (?,?,?,?)',
                   (name, timestamp, timestamp, data['head_id']))
        self._append(db, name, data['nodes'], data['compactions'])

    def create(self, name, data=None, *, if_absent=False):
        data = data or dict(version=3, head_id=None, nodes=[], compactions=[])
        self._validate(name, data)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM sessions WHERE id=?', (name,)).fetchone():
                if if_absent:
                    return
                raise ValueError(f'Session already exists: {name}')
            self._insert(db, name, data)

    def load(self, name, *, lazy=False):
        with self.connect() as db:
            db.execute('BEGIN')  # One consistent read snapshot.
            row = db.execute('SELECT head_id,revision FROM sessions WHERE id=?', (name,)).fetchone()
            if row is None:
                raise SessionNotFound(f'Session not found: {name}')
            node_rows = db.execute(
                'SELECT id,parent_id,created_at FROM message_nodes WHERE session_id=? ORDER BY position',
                (name,)).fetchall()
            compact_rows = db.execute(
                'SELECT id,through_id,created_at FROM compactions WHERE session_id=? ORDER BY position',
                (name,)).fetchall()
            nodes = [LazyRecord(dict(id=nid, parent_id=parent, created_at=created),
                                lambda nid=nid: {'message': self._read_record('message_nodes', 'message', name, nid)})
                     for nid, parent, created in node_rows]
            compactions = [LazyRecord(dict(id=cid, through_id=through, created_at=created),
                                      lambda cid=cid: self._read_record('compactions', 'data', name, cid))
                           for cid, through, created in compact_rows]
            if not lazy:
                # Use the same snapshot for eager readers and migrations.
                for node in nodes:
                    encoded = db.execute('SELECT message FROM message_nodes WHERE session_id=? AND id=?',
                                         (name, node['id'])).fetchone()[0]
                    node.update(message=self.payloads.loads(encoded))
                for item in compactions:
                    encoded = db.execute('SELECT data FROM compactions WHERE session_id=? AND id=?',
                                         (name, item['id'])).fetchone()[0]
                    item.update(self.payloads.loads(encoded))
                nodes = [dict(node) for node in nodes]
                compactions = [dict(item) for item in compactions]

        return dict(version=3, head_id=row[0], nodes=nodes, compactions=compactions), row[1]

    def _read_record(self, table, column, name, identifier):
        with self.connect() as db:
            row = db.execute(f'SELECT {column} FROM {table} WHERE session_id=? AND id=?',
                             (name, identifier)).fetchone()
            if row is None:
                raise SessionConflict('Session history was deleted; reload before continuing')
            return self.payloads.loads(row[0])

    def append(self, name, revision, head_id, nodes, compactions, node_offset, compact_offset):
        with self.connect() as db:
            updated = db.execute('''UPDATE sessions SET head_id=?, updated_at=?, revision=revision+1
                WHERE id=? AND revision=?''', (head_id, now(), name, revision))
            if updated.rowcount != 1:
                raise SessionConflict('Session changed in another process or was deleted; reload before continuing')
            self._append(db, name, nodes, compactions, node_offset, compact_offset)
            if head_id is not None and not db.execute(
                'SELECT 1 FROM message_nodes WHERE session_id=? AND id=?', (name, head_id)
            ).fetchone():
                raise ValueError('Invalid session head')
        return revision + 1

    def list_names(self):
        with self.connect() as db:
            return [row[0] for row in db.execute('SELECT id FROM sessions ORDER BY updated_at DESC,id')]


class LazyRecord(dict):
    """Tree metadata available immediately; immutable payload fetched on access."""
    def __init__(self, metadata, loader):
        super().__init__(metadata)
        self.loader = loader

    def __getitem__(self, key):
        if key not in self and self.loader is not None:
            self.update(self.loader())
            self.loader = None
        return super().__getitem__(key)

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default
