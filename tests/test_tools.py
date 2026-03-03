import tempfile
import unittest
from pathlib import Path

from codeops.tools import ToolError, list_files, run_shell


class TestTools(unittest.TestCase):
    def test_list_files_skips_large_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "a").mkdir()
            (root / "a" / "x.txt").write_text("x", encoding="utf-8")
            (root / "node_modules").mkdir()
            (root / "node_modules" / "y.txt").write_text("y", encoding="utf-8")

            files = list_files(".", root=root, max_entries=50)
            self.assertIn("a/x.txt", files)
            self.assertNotIn("node_modules/y.txt", files)

    def test_run_shell_blocks_non_allowlisted(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with self.assertRaises(ToolError):
                run_shell("curl --version", root=root)

    def test_run_shell_requires_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "x.txt").write_text("x", encoding="utf-8")
            with self.assertRaises(ToolError):
                run_shell("rm x.txt", root=root, confirm=False)
