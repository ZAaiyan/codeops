import tempfile
import unittest
from pathlib import Path

from codeops.memory_store import MemoryStore


class TestMemoryStore(unittest.TestCase):
    def test_undo_redo(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            p = root / "a.txt"
            p.write_text("v1", encoding="utf-8")

            store = MemoryStore(workspace=root)
            store.record_file_change(path="a.txt", before="v1", after="v2")
            p.write_text("v2", encoding="utf-8")

            self.assertEqual(store.undo(), "ok: undo a.txt")
            self.assertEqual(p.read_text(encoding="utf-8"), "v1")

            self.assertEqual(store.redo(), "ok: redo a.txt")
            self.assertEqual(p.read_text(encoding="utf-8"), "v2")
