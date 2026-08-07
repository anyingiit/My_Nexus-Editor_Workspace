from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from agents_v3.context import ContextManifest, pack_context


class ContextTests(unittest.TestCase):
    def test_order_and_checksums_are_deterministic(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "AGENTS.md").write_text("core", encoding="utf-8")
            (root / "style.md").write_text("style", encoding="utf-8")
            manifest = ContextManifest("AGENTS.md", ("style.md",), 100)
            first = pack_context(root, manifest)
            second = pack_context(root, manifest)
            self.assertEqual(first, second)
            self.assertLess(first[0].index("AGENTS.md"), first[0].index("style.md"))

    def test_overflow_fails_instead_of_silent_truncation(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "AGENTS.md").write_text("long", encoding="utf-8")
            with self.assertRaises(ValueError):
                pack_context(root, ContextManifest("AGENTS.md", (), 2))

    def test_path_escape_is_rejected(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(ValueError):
                pack_context(root, ContextManifest("../secret", (), 100))


if __name__ == "__main__":
    unittest.main()
