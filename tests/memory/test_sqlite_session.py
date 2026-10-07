import json
from pathlib import Path
import tempfile
import unittest
import sqlite3
from unittest.mock import patch

from picli.memory.session import Session
from picli.skills.registry import SkillRegistry
from picli.memory.sqlite_session import SQLiteSession
from picli.memory.compaction import CompactionPlan
from picli.memory.session_database import SessionConflict, SessionDatabase
from picli.memory.payload_store import PayloadStore


class SQLiteSessionTests(unittest.TestCase):
    def test_stale_writer_cannot_overwrite_or_insert_messages(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            skills = SkillRegistry(root)
            first = SQLiteSession(root, 'shared', skills)
            second = SQLiteSession(root, 'shared', skills)
            first.messages.append({'role': 'user', 'content': 'winner'})
            first.save()
            second.messages.append({'role': 'user', 'content': 'stale'})
            with self.assertRaises(SessionConflict):
                second.save()
            restored = SQLiteSession(root, 'shared', skills)
            self.assertEqual([m['content'] for m in restored.messages], ['winner'])
            self.assertEqual(restored.revision, 1)

    def test_save_does_not_rewrite_existing_nodes_and_rolls_back_on_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session = SQLiteSession(root, 'main', SkillRegistry(root))
            session.messages.append({'role': 'user', 'content': 'first'})
            session.save()
            before, revision = session.database.load('main')
            with session.connect() as db:
                db.execute("CREATE TRIGGER immutable_nodes BEFORE UPDATE ON message_nodes BEGIN SELECT RAISE(ABORT,'immutable'); END")
                db.execute("CREATE TRIGGER immutable_deletes BEFORE DELETE ON message_nodes BEGIN SELECT RAISE(ABORT,'immutable'); END")
            session.messages.append({'role': 'assistant', 'content': 'second'})
            original = session.database._append
            def fail_after_insert(*args, **kwargs):
                original(*args, **kwargs)
                raise RuntimeError('simulated interruption')
            with patch.object(session.database, '_append', side_effect=fail_after_insert):
                with self.assertRaises(RuntimeError):
                    session.save()
            self.assertEqual(session.database.load('main'), (before, revision))
            self.assertEqual(session.revision, revision)
            with patch.object(session.payloads, 'dumps', wraps=session.payloads.dumps) as encode:
                session.save()
                self.assertEqual(encode.call_count, 1)
            self.assertEqual(len(session.database.load('main')[0]['nodes']), 2)

    def test_old_sqlite_migration_preserves_tree_and_large_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            skills = SkillRegistry(root)
            legacy = Session(root, 'old', skills)
            legacy.messages.append({'role': 'user', 'content': 'old text' * 4000})
            state = dict(version=3, head_id=legacy.head_id, nodes=legacy.nodes, compactions=[])
            path = root / '.pi-python' / 'sessions.sqlite3'
            payloads = PayloadStore(path.parent / 'artifacts')
            with sqlite3.connect(path) as db:
                db.execute('CREATE TABLE sessions(id TEXT PRIMARY KEY,data TEXT NOT NULL)')
                db.execute('INSERT INTO sessions VALUES (?,?)', ('old', payloads.dumps(state)))
            migrated = SQLiteSession(root, 'old', skills)
            self.assertEqual(migrated.nodes, legacy.nodes)
            self.assertEqual(migrated.head_id, legacy.head_id)
            with migrated.connect() as db:
                self.assertNotIn('data', [row[1] for row in db.execute('PRAGMA table_info(sessions)')])
                self.assertEqual(db.execute('SELECT version FROM session_storage_meta').fetchone()[0], 1)
            self.assertEqual(SessionDatabase(path).load('old')[0], state)

    def test_invalid_migration_rolls_back_schema_and_records(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'sessions.sqlite3'
            bad = json.dumps(dict(version=3, head_id='missing', nodes=[], compactions=[]))
            with sqlite3.connect(path) as db:
                db.execute('CREATE TABLE sessions(id TEXT PRIMARY KEY,data TEXT NOT NULL)')
                db.execute('INSERT INTO sessions VALUES (?,?)', ('bad', bad))
            with self.assertRaises(ValueError):
                SessionDatabase(path)
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute('SELECT data FROM sessions').fetchone()[0], bad)
                self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='message_nodes'").fetchone())

    def test_import_preserves_source_and_sqlite_wins_on_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            skills = SkillRegistry(root)
            legacy = Session(root, 'legacy', skills)
            legacy.messages.append({'role': 'user', 'content': 'original'})
            legacy.save()
            path = legacy.directory / 'legacy.json'
            original = path.read_bytes()
            session = SQLiteSession(root, 'legacy', skills)
            session.messages.append({'role': 'assistant', 'content': 'new reply'})
            session.save()
            self.assertEqual(path.read_bytes(), original)
            restored = SQLiteSession(root, 'legacy', skills)
            self.assertEqual(restored.messages[-1]['content'], 'new reply')
            self.assertEqual(restored.list_names(), ['legacy'])

    def test_large_content_compaction_fork_and_checkout_survive_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            skills = SkillRegistry(root)
            session = SQLiteSession(root, 'main', skills)
            large = 'large result ' * 2000
            session.messages.extend([{'role': 'user', 'content': 'first'},
                                     {'role': 'assistant', 'content': large},
                                     {'role': 'user', 'content': 'second'}])
            first = session.nodes[0]['id']
            session.apply_compaction(CompactionPlan(session.messages[:2], session.messages[2:], 100), 'summary')
            session.save()
            session = SQLiteSession(root, 'main', skills)
            self.assertEqual(session.summary, 'summary')
            self.assertEqual(session.all_messages[1]['content'], large)
            with session.connect() as db:
                self.assertNotIn(large, str(db.execute('SELECT message FROM message_nodes').fetchall()))
            session.fork('branch')
            self.assertEqual(session.summary, 'summary')
            session.checkout(first)
            self.assertEqual(session.summary, '')
            self.assertEqual(len(SQLiteSession(root, 'branch', skills).messages), 1)
            self.assertEqual(set(session.list_names()), {'branch', 'main'})
            with self.assertRaises(ValueError):
                session.fork('main')
            self.assertFalse((session.directory / 'branch.json').exists())


if __name__ == '__main__':
    unittest.main()
