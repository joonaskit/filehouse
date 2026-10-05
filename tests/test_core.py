"""Unit tests for FileHouse core logic."""

import unittest
import tempfile
from pathlib import Path
from filehouse.core.history import NavigationHistory
from filehouse.core.file_ops import format_size, get_mime_type


class TestCoreLogic(unittest.TestCase):

    def test_history_navigation(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            dir_a = tmp_path / "dir_a"
            dir_b = tmp_path / "dir_b"
            dir_a.mkdir()
            dir_b.mkdir()

            history = NavigationHistory(initial_path=dir_a)
            self.assertEqual(history.current, dir_a)
            self.assertFalse(history.can_go_back)
            self.assertFalse(history.can_go_forward)

            history.navigate_to(dir_b)
            self.assertEqual(history.current, dir_b)
            self.assertTrue(history.can_go_back)
            self.assertFalse(history.can_go_forward)

            back_result = history.back()
            self.assertEqual(back_result, dir_a)
            self.assertEqual(history.current, dir_a)
            self.assertFalse(history.can_go_back)
            self.assertTrue(history.can_go_forward)

            fwd_result = history.forward()
            self.assertEqual(fwd_result, dir_b)
            self.assertEqual(history.current, dir_b)

    def test_format_size(self):
        self.assertEqual(format_size(0), "0 B")
        self.assertEqual(format_size(500), "500 B")
        self.assertEqual(format_size(1024), "1.0 KB")
        self.assertEqual(format_size(1024 * 1024), "1.0 MB")
        self.assertEqual(format_size(1024 * 1024 * 1024), "1.0 GB")

    def test_mime_type(self):
        txt_path = Path("test.txt")
        self.assertIn("text", get_mime_type(txt_path))


if __name__ == "__main__":
    unittest.main()
