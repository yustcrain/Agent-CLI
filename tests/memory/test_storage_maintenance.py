import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from picli.memory.compaction import CompactionPlan
from picli.memory.session_database import SessionDatabase
from picli.skills.registry import SkillRegistry
from picli.memory.sqlite_session import SQLiteSession
from picli.memory.maintenance import backup, collect


class StorageMaintenanceTests(unittest.TestCase):
    def test_old_payloads_load_only_when_history_is_requested(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            skills = SkillRegistry(root)
            session = SQLiteSession(root, 'main', skills)
            large = 'old evidence ' * 2000
            session.messages.extend([{'role': 'user', 'content': 'old question'},
                                     {'role': 'assistant', 'content': large},
                                     {'role': 'user', 'content': 'current question'}])
            session.apply_compaction(CompactionPlan(session.messages[:2], session.messages[2:], 100), 'old summary')
            session.save()
            db = SessionDatabase(session.database_path)
            with patch.object(db.payloads, 'loads', wraps=db.payloads.loads) as decode:
                restored = SQLiteSession(root, 'main', skills, db)
                self.assertEqual(decode.call_count, 1)  # Only the current message.
                self.assertNotIn('message', restored.nodes[1])
                self.assertEqual(restored.summary, 'old summary')
                self.assertEqual(decode.call_count, 2)
                self.assertEqual(restored.all_messages[1]['content'], large)
                self.assertIn('message', restored.nodes[1])
                restored.checkout(restored.nodes[1]['id'])
                self.assertEqual(restored.summary, 'old summary')
                restored.checkout(restored.nodes[0]['id'])
                self.assertEqual(restored.summary, '')

    def test_gc_preserves_references_in_both_databases_and_dry_run(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session = SQLiteSession(root, 'cli', SkillRegistry(root))
            session.messages.append({'role': 'user', 'content': 'cli' * 10000})
            session.save()
            archive_db = SessionDatabase(root / '.pi-python' / 'archive.sqlite3')
            archived = SQLiteSession(root, 'archived', SkillRegistry(root), archive_db)
            archived.messages.append({'role': 'assistant', 'content': 'archived' * 10000})
            archived.save()
            orphan = session.payloads.dumps({'content': 'orphan' * 10000})
            digest = json.loads(orphan)['references'][0]['sha256']
            self.assertEqual(collect(root)['orphan_files'], 1)
            self.assertTrue((session.payloads.directory / digest).exists())
            report = collect(root, apply=True)
            self.assertEqual(report['databases'], 2)
            self.assertEqual(report['live'], 2)
            self.assertFalse((session.payloads.directory / digest).exists())
            self.assertEqual(collect(root)['orphan_files'], 0)
            self.assertEqual(SQLiteSession(root, 'cli', SkillRegistry(root)).messages[0]['content'], 'cli' * 10000)
            self.assertEqual(SQLiteSession(root, 'archived', SkillRegistry(root), archive_db).messages[0]['content'], 'archived' * 10000)

    def test_backup_restores_cli_sessions_large_text_and_branch_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'live'
            session = SQLiteSession(root, 'cli', SkillRegistry(root))
            session.messages.extend([{'role': 'user', 'content': 'question'},
                                     {'role': 'assistant', 'content': 'large' * 10000}])
            session.save()
            session.checkout(session.nodes[0]['id'])
            session.messages.append({'role': 'assistant', 'content': 'branch' * 10000})
            session.save()
            snapshot_head = session.head_id
            destination = Path(directory) / 'backup'
            report = backup(root, destination)
            self.assertEqual(report['databases'], 1)
            session.messages.append({'role': 'user', 'content': 'after snapshot'})
            session.save()
            restored = SQLiteSession(destination, 'cli', SkillRegistry(destination))
            self.assertEqual(len(restored.messages), 2)
            self.assertEqual(restored.head_id, snapshot_head)
            self.assertEqual(restored.messages[-1]['content'], 'branch' * 10000)
            restored.checkout(restored.nodes[1]['id'])
            self.assertEqual(restored.messages[-1]['content'], 'large' * 10000)
            with self.assertRaises(ValueError):
                backup(root, destination)

    def test_missing_artifact_aborts_backup_and_gc_without_deletion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'live'
            session = SQLiteSession(root, 'main', SkillRegistry(root))
            session.messages.append({'role': 'user', 'content': 'live' * 10000})
            session.save()
            live = next(session.payloads.directory.iterdir())
            live.unlink()
            orphan = session.payloads.dumps({'content': 'orphan' * 10000})
            digest = json.loads(orphan)['references'][0]['sha256']
            destination = Path(directory) / 'backup'
            with self.assertRaises(ValueError):
                backup(root, destination)
            self.assertFalse(destination.exists())
            with self.assertRaises(ValueError):
                collect(root, apply=True)
            self.assertTrue((session.payloads.directory / digest).exists())

    def test_gc_waits_for_uncommitted_artifact_reference(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session = SQLiteSession(root, 'main', SkillRegistry(root))
            started, finished = threading.Event(), threading.Event()
            reports, errors = [], []
            def gc():
                started.set()
                try:
                    reports.append(collect(root, apply=True))
                except Exception as error:
                    errors.append(error)
                finally:
                    finished.set()
            with session.connect() as db:
                payload = session.payloads.dumps({'content': 'pending' * 10000})
                db.execute('CREATE TABLE extra(data TEXT)')
                worker = threading.Thread(target=gc)
                worker.start()
                self.assertTrue(started.wait(2))
                self.assertFalse(finished.wait(.1))
                db.execute('INSERT INTO extra VALUES (?)', (payload,))
            worker.join(3)
            self.assertFalse(worker.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual(reports[0]['orphan_files'], 0)


if __name__ == '__main__':
    unittest.main()
