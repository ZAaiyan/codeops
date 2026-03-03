import tempfile
import unittest
from pathlib import Path

from codeops.config import load_merged_config


class TestConfig(unittest.TestCase):
    def test_load_workspace_codeops_yaml(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".codeops.yaml").write_text("model: ep-xxx\nmax_iterations: 3\n", encoding="utf-8")
            cfg = load_merged_config(workspace=root)
            self.assertEqual(cfg.get("model"), "ep-xxx")
            self.assertEqual(cfg.get("max_iterations"), 3)
