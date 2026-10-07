"""Session adapter using incremental SQLite writes and optimistic concurrency."""
import uuid

from picli.memory.session import Session
from picli.memory.session_database import SessionDatabase, SessionNotFound


class SQLiteSession(Session):
    def __init__(self, workspace, session, skills, database=None, *, create_missing=True):
        self.database = database or SessionDatabase(workspace / '.pi-python' / 'sessions.sqlite3')
        self.database_path = self.database.path
        self.payloads = self.database.payloads
        self.create_missing = create_missing
        super().__init__(workspace, session, skills)

    def connect(self):
        return self.database.connect()

    def select(self, name=None):
        name = self._valid_name(name or uuid.uuid4().hex)
        try:
            data, revision = self.database.load(name, lazy=True)
        except SessionNotFound:
            if not self.create_missing:
                raise
            super().select(name)  # Preserve legacy JSON as a migration backup.
            self.database.create(name, dict(version=3, head_id=self.head_id,
                                           nodes=self.nodes, compactions=self.compactions), if_absent=True)
            data, revision = self.database.load(name, lazy=True)
        # Rows were validated when written. Avoid decoding old/off-branch payloads.
        self.name = name
        self.nodes = data['nodes']
        self.head_id = data['head_id']
        self.compactions = data['compactions']
        self._refresh_messages()
        self.revision = revision
        self._node_count = len(self.nodes)
        self._compaction_count = len(self.compactions)

    def save(self):
        # Existing tree records are immutable; persist only new rows and the head.
        self.revision = self.database.append(
            self.name, self.revision, self.head_id,
            self.nodes[self._node_count:], self.compactions[self._compaction_count:],
            self._node_count, self._compaction_count,
        )
        self._node_count = len(self.nodes)
        self._compaction_count = len(self.compactions)

    def list_names(self):
        names = self.database.list_names()
        return names + sorted(set(super().list_names()) - set(names)) if self.create_missing else names

    def _create_fork(self, name, data):
        self.database.create(name, data)
