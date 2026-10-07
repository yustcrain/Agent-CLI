import os
import tempfile
import unittest
from pathlib import Path
from storage import read_text, resolve_under

class Tests(unittest.TestCase):
    def test_nested_read_and_traversal(self):
        with tempfile.TemporaryDirectory() as d:
            base=Path(d); root=base/"data"; (root/"nested").mkdir(parents=True); (root/"nested"/"a.txt").write_text("ok",encoding="utf-8"); (base/"secret").write_text("no")
            old=os.getcwd(); os.chdir(base)
            try:
                self.assertEqual(read_text("data","nested/a.txt"),"ok")
                for bad in ("../secret",str(base/"secret")):
                    with self.assertRaises(ValueError): resolve_under("data",bad)
                if hasattr(os,"symlink"):
                    os.symlink(base/"secret",root/"link")
                    with self.assertRaises(ValueError): read_text("data","link")
            finally: os.chdir(old)

unittest.main()
