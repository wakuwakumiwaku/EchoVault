import os
from pathlib import Path
import shutil
import stat
import tempfile
import unittest
from unittest.mock import patch

from echo_vault import SafeBackupHandler


class AtomicSyncTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="echovault-atomic-test-")
        self.addCleanup(temporary.cleanup)
        self.source = Path(temporary.name) / "source"
        self.backup = Path(temporary.name) / "backup"
        self.source.mkdir()
        self.backup.mkdir()
        self.source_file = self.source / "notes.txt"
        self.backup_file = self.backup / "notes.txt"
        self.source_file.write_bytes(b"complete new contents")
        self.backup_file.write_bytes(b"previous intact backup")
        self.handler = SafeBackupHandler(str(self.source), str(self.backup))

    def interrupted_copy(self, source, destination):
        Path(destination).write_bytes(b"partial")
        raise OSError("simulated interrupted copy")

    def test_failed_copy_preserves_existing_backup(self):
        before = self.backup_file.stat()
        with patch("echo_vault.shutil.copy2", side_effect=self.interrupted_copy):
            with self.assertLogs(level="ERROR"):
                self.handler.sync_file(str(self.source_file))

        self.assertEqual(self.backup_file.read_bytes(), b"previous intact backup")
        self.assertEqual(self.backup_file.stat().st_mtime_ns, before.st_mtime_ns)
        self.assertEqual(list(self.backup.iterdir()), [self.backup_file])
        self.assertEqual(self.source_file.read_bytes(), b"complete new contents")

    def test_failed_first_copy_leaves_no_incomplete_backup(self):
        self.backup_file.unlink()
        with patch("echo_vault.shutil.copy2", side_effect=self.interrupted_copy):
            with self.assertLogs(level="ERROR"):
                self.handler.sync_file(str(self.source_file))

        self.assertFalse(self.backup_file.exists())
        self.assertEqual(list(self.backup.iterdir()), [])

    def test_failed_replacement_preserves_existing_backup_and_cleans_staging(self):
        with patch("echo_vault.os.replace", side_effect=OSError("simulated replace failure")):
            with self.assertLogs(level="ERROR"):
                self.handler.sync_file(str(self.source_file))

        self.assertEqual(self.backup_file.read_bytes(), b"previous intact backup")
        # History is saved before replacement. A later replacement failure can
        # leave a duplicate snapshot, but never removes the active backup.
        archived = list((self.backup / "_deleted_history").glob("*/notes.txt"))
        self.assertEqual(len(archived), 1)
        self.assertEqual(archived[0].read_bytes(), b"previous intact backup")
        self.assertEqual(list(self.backup.rglob(".echovault-*.tmp")), [])

    def test_successful_sync_preserves_contents_and_metadata(self):
        os.utime(self.source_file, (1_600_000_000, 1_600_000_000))
        self.source_file.chmod(0o640)
        copied_to = []
        real_copy = shutil.copy2

        def copy_to_staging(source, destination):
            copied_to.append(Path(destination))
            self.assertEqual(self.backup_file.read_bytes(), b"previous intact backup")
            self.assertNotEqual(Path(destination), self.backup_file)
            if Path(source) == self.source_file:
                self.assertEqual(Path(destination).parent, self.backup_file.parent)
                self.assertTrue(Path(destination).name.startswith(".echovault-"))
            else:
                self.assertEqual(Path(source), self.backup_file)
                self.assertEqual(Path(destination).parent.parent, self.backup / "_deleted_history")
            result = real_copy(source, destination)
            self.assertEqual(self.backup_file.read_bytes(), b"previous intact backup")
            return result

        with patch("echo_vault.shutil.copy2", side_effect=copy_to_staging):
            self.handler.sync_file(str(self.source_file))

        self.assertEqual(len(copied_to), 2)
        self.assertEqual(self.backup_file.read_bytes(), self.source_file.read_bytes())
        self.assertEqual(self.backup_file.stat().st_mtime_ns, self.source_file.stat().st_mtime_ns)
        self.assertEqual(
            stat.S_IMODE(self.backup_file.stat().st_mode),
            stat.S_IMODE(self.source_file.stat().st_mode),
        )
        archived = list((self.backup / "_deleted_history").glob("*/notes.txt"))
        self.assertEqual(len(archived), 1)
        self.assertEqual(archived[0].read_bytes(), b"previous intact backup")
        self.assertEqual(list(self.backup.rglob(".echovault-*.tmp")), [])

    def test_unchanged_file_does_not_copy_or_replace(self):
        self.backup_file.write_bytes(self.source_file.read_bytes())
        with patch("echo_vault.shutil.copy2") as copy, patch("echo_vault.os.replace") as replace:
            self.handler.sync_file(str(self.source_file))

        copy.assert_not_called()
        replace.assert_not_called()
        self.assertEqual(list(self.backup.iterdir()), [self.backup_file])


if __name__ == "__main__":
    unittest.main()
