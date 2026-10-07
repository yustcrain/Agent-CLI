import json
from pathlib import Path
import tempfile
import unittest

from picli.memory.payload_store import PayloadStore


class PayloadStoreTests(unittest.TestCase):
    def test_large_nested_text_roundtrips_after_restart_and_deduplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'artifacts'
            text = '工具输出' * 100
            value = {'head_id': 'node1', 'nodes': [{'content': text}], 'summary': text}
            encoded = PayloadStore(root, 64).dumps(value)
            self.assertNotIn(text, encoded)
            self.assertIn('node1', encoded)
            self.assertEqual(len(list(root.iterdir())), 1)
            self.assertEqual(PayloadStore(root).loads(encoded), value)

    def test_legacy_json_is_readable(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PayloadStore(Path(directory))
            value = {'version': 3, 'nodes': [], 'compactions': []}
            self.assertEqual(store.loads(json.dumps(value)), value)

    def test_corrupt_artifact_is_not_silently_used(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = PayloadStore(root, 4)
            encoded = store.dumps({'content': 'long text'})
            next(root.iterdir()).write_text('corrupted')
            with self.assertRaisesRegex(ValueError, 'integrity'):
                store.loads(encoded)


if __name__ == '__main__':
    unittest.main()
