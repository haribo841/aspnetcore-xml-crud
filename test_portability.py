"""Standalone checkout boundaries, without production data or model downloads."""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from batch.common import APP, DEFAULT_ROOT, application_python, defaults
from batch.store import Store
from batch.worker import launch


class PortabilityTests(unittest.TestCase):
    def test_integration_validator_accepts_only_a_fresh_directory(self):
        from validate_batch_runtime import check_fresh_validation_root
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            check_fresh_validation_root(root)
            nonexistent = root / 'new-test'
            check_fresh_validation_root(nonexistent)
            self.assertFalse(nonexistent.exists())

    def test_integration_validator_cannot_reuse_a_nested_queue_or_results(self):
        from validate_batch_runtime import check_fresh_validation_root
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            queue_file = root / 'crash-resume' / 'kolejka.sqlite3'
            queue_file.parent.mkdir()
            queue_file.write_bytes(b'previous queue')
            with self.assertRaisesRegex(ValueError, 'nowy albo pusty'):
                check_fresh_validation_root(root)
            self.assertEqual(queue_file.read_bytes(), b'previous queue')

    def test_current_interpreter_is_used_outside_the_checkout_venv(self):
        self.assertEqual(Path(application_python()).parent, Path(sys.executable).parent)
        with tempfile.TemporaryDirectory() as folder, patch("batch.worker.subprocess.Popen") as popen:
            launch(Path(folder))
        command = popen.call_args.args[0]
        self.assertEqual(command[0], application_python())
        self.assertEqual(Path(command[1]), APP / "kolejka.py")

    def test_configured_root_can_be_read_without_opening_the_production_queue(self):
        with tempfile.TemporaryDirectory() as folder:
            queue = Store(folder)
            before = queue.control()
            result = subprocess.run([application_python(), str(APP / "kolejka.py"), "--root", folder, "status"],
                                    check=True, capture_output=True, text=True, cwd=APP)
            self.assertIn('"counts": {}', result.stdout)
            self.assertEqual(before, queue.control())

    def test_default_paths_are_owned_by_this_checkout_and_user(self):
        config = defaults()
        self.assertTrue(Path(config["model"]).is_relative_to(APP))
        self.assertTrue(Path(config["diar_python"]).is_relative_to(APP))
        self.assertTrue(Path(config["uvr_python"]).is_relative_to(APP))
        self.assertNotIn("Applio", str(DEFAULT_ROOT))


if __name__ == "__main__":
    unittest.main()
