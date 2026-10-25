"""Data-safety regressions through watchdog's registered modified-event caller."""
from datetime import datetime
import os
from pathlib import Path
import shutil
import stat
import tempfile
import unittest
from unittest.mock import patch

from watchdog.events import DirModifiedEvent, FileDeletedEvent, FileModifiedEvent

from echo_vault import SafeBackupHandler


class ModifiedHistoryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="echovault-history-test-")
        self.addCleanup(temporary.cleanup)
        self.source = Path(temporary.name) / "source"
        self.backup = Path(temporary.name) / "backup"
        self.source.mkdir()
        self.backup.mkdir()
        self.handler = SafeBackupHandler(str(self.source), str(self.backup))
        self.source_file = self.source / "notes.txt"
        self.backup_file = self.backup / "notes.txt"
        self.source_file.write_bytes(b"new source bytes\x00\xff")
        self.backup_file.write_bytes(b"actual previous backup\x00\xfe")
        # Only the watcher debounce and wall clock are replaced; dispatch,
        # on_modified, sync_file, hashing, and history handling all run for real.
        sleep = patch("echo_vault.time.sleep")
        sleep.start()
        self.addCleanup(sleep.stop)
        clock = patch("echo_vault.datetime")
        clock.start().now.return_value = datetime(2026, 1, 2, 3, 4, 5)
        self.addCleanup(clock.stop)
        self.history = self.backup / "_deleted_history" / "2026-01-02_03-04-05"

    def modify(self, source=None):
        self.handler.dispatch(FileModifiedEvent(str(source or self.source_file)))

    def history_contents(self):
        if not self.history.exists():
            return {}
        return {path.name: path.read_bytes() for path in self.history.iterdir()}

    def assert_no_staging(self):
        self.assertEqual(list(self.backup.rglob(".echovault-*.tmp")), [])

    def assert_no_sync_success(self, captured):
        self.assertTrue(any(record.levelno >= 40 for record in captured.records))
        self.assertFalse(any(record.getMessage().startswith("SYNCED:")
                             for record in captured.records))

    def test_modified_event_archives_actual_previous_backup_before_publishing(self):
        previous = self.backup_file.read_bytes()
        self.modify()
        self.assertEqual(self.backup_file.read_bytes(), self.source_file.read_bytes())
        self.assertEqual(self.history_contents(), {"notes.txt": previous})
        self.assert_no_staging()

    def test_repeated_modifications_and_same_basename_collisions_keep_all_versions(self):
        self.backup_file.unlink()
        unrelated = self.backup / "notes_1.txt"
        unrelated.write_bytes(b"unrelated deleted backup")
        self.handler.dispatch(FileDeletedEvent(str(self.source / "notes_1.txt")))
        self.assertFalse(unrelated.exists())
        a_source = self.source / "a" / "notes.txt"
        b_source = self.source / "b" / "notes.txt"
        for source, first in ((a_source, b"a first"), (b_source, b"b first")):
            source.parent.mkdir()
            source.write_bytes(first)
            self.modify(source)
        a_source.write_bytes(b"a second")
        self.modify(a_source)
        a_source.write_bytes(b"a third")
        self.modify(a_source)
        b_source.write_bytes(b"b second")
        self.modify(b_source)
        self.assertEqual(self.history_contents(), {
            "notes_1.txt": b"unrelated deleted backup", "notes.txt": b"a first",
            "notes_2.txt": b"a second", "notes_3.txt": b"b first",
        })
        self.assertEqual((self.backup / "a" / "notes.txt").read_bytes(), b"a third")
        self.assertEqual((self.backup / "b" / "notes.txt").read_bytes(), b"b second")
        self.assert_no_staging()

    def test_unchanged_content_does_not_copy_replace_or_create_history(self):
        self.backup_file.write_bytes(self.source_file.read_bytes())
        with patch("echo_vault.shutil.copy2", wraps=shutil.copy2) as copy:
            with patch("echo_vault.os.replace", wraps=os.replace) as replace:
                self.modify()
        copy.assert_not_called()
        replace.assert_not_called()
        self.assertFalse((self.backup / "_deleted_history").exists())
        self.assert_no_staging()

    def test_duplicate_event_after_change_does_not_add_history(self):
        previous = self.backup_file.read_bytes()
        self.modify()
        self.modify()
        self.assertEqual(self.history_contents(), {"notes.txt": previous})
        self.assertEqual(self.backup_file.read_bytes(), self.source_file.read_bytes())

    def test_first_backup_copies_metadata_without_history(self):
        self.backup_file.unlink()
        nested = self.source / "nested" / "first.txt"
        nested.parent.mkdir()
        nested.write_bytes(b"first complete contents")
        nested.chmod(0o640)
        os.utime(nested, (1_600_000_000, 1_600_000_000))
        self.modify(nested)
        backup = self.backup / "nested" / "first.txt"
        self.assertEqual(backup.read_bytes(), nested.read_bytes())
        self.assertEqual(backup.stat().st_mtime_ns, nested.stat().st_mtime_ns)
        self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o640)
        self.assertFalse((self.backup / "_deleted_history").exists())
        self.assert_no_staging()

    def test_failed_source_copy_keeps_active_backup_and_creates_no_history(self):
        previous = self.backup_file.read_bytes()
        before = self.backup_file.stat()

        def fail_copy(source, destination):
            self.assertEqual(Path(source), self.source_file)
            Path(destination).write_bytes(b"partial source copy")
            raise OSError("injected source copy failure")

        with patch("echo_vault.shutil.copy2", side_effect=fail_copy):
            with self.assertLogs(level="INFO") as captured:
                self.modify()
        self.assert_no_sync_success(captured)
        self.assertEqual(self.backup_file.read_bytes(), previous)
        self.assertEqual(self.backup_file.stat().st_mtime_ns, before.st_mtime_ns)
        self.assertFalse((self.backup / "_deleted_history").exists())
        self.assert_no_staging()

    def test_failed_first_source_copy_publishes_no_partial_file(self):
        self.backup_file.unlink()

        def fail_copy(source, destination):
            Path(destination).write_bytes(b"partial source copy")
            raise OSError("injected first copy failure")

        with patch("echo_vault.shutil.copy2", side_effect=fail_copy):
            with self.assertLogs(level="INFO") as captured:
                self.modify()
        self.assert_no_sync_success(captured)
        self.assertEqual(list(self.backup.iterdir()), [])

    def test_failed_replacement_keeps_active_and_complete_history_snapshot(self):
        previous = self.backup_file.read_bytes()
        before = self.backup_file.stat()

        def fail_replace(source, destination):
            self.assertEqual(Path(destination), self.backup_file)
            self.assertEqual(self.backup_file.read_bytes(), previous)
            self.assertEqual(Path(source).read_bytes(), self.source_file.read_bytes())
            self.assertEqual(self.history_contents(), {"notes.txt": previous})
            raise OSError("injected replacement failure")

        with patch("echo_vault.os.replace", side_effect=fail_replace) as replace:
            with self.assertLogs(level="INFO") as captured:
                self.modify()
        replace.assert_called_once()
        self.assert_no_sync_success(captured)
        self.assertEqual(self.backup_file.read_bytes(), previous)
        self.assertEqual(self.backup_file.stat().st_mtime_ns, before.st_mtime_ns)
        self.assertEqual(self.history_contents(), {"notes.txt": previous})
        self.assert_no_staging()

    def test_failed_first_replacement_publishes_no_partial_file(self):
        self.backup_file.unlink()
        with patch("echo_vault.os.replace", side_effect=OSError("injected first replacement failure")):
            with self.assertLogs(level="INFO") as captured:
                self.modify()
        self.assert_no_sync_success(captured)
        self.assertEqual(list(self.backup.iterdir()), [])

    def test_failed_history_copy_does_not_discard_active_or_log_sync_success(self):
        previous = self.backup_file.read_bytes()
        before = self.backup_file.stat()
        real_copy = shutil.copy2
        attempted_history = []

        def fail_history_copy(source, destination):
            if Path(source) == self.backup_file:
                attempted_history.append(Path(destination))
                Path(destination).write_bytes(b"partial history copy")
                raise OSError("injected history copy failure")
            return real_copy(source, destination)

        with patch("echo_vault.shutil.copy2", side_effect=fail_history_copy):
            with patch("echo_vault.os.replace", wraps=os.replace) as replace:
                with self.assertLogs(level="INFO") as captured:
                    self.modify()
        self.assertEqual(len(attempted_history), 1)
        replace.assert_not_called()
        self.assert_no_sync_success(captured)
        self.assertEqual(self.backup_file.read_bytes(), previous)
        self.assertEqual(self.backup_file.stat().st_mtime_ns, before.st_mtime_ns)
        self.assertEqual(self.history_contents(), {})
        self.assert_no_staging()

    def test_history_directory_failure_keeps_active_and_cleans_staging(self):
        previous = self.backup_file.read_bytes()
        # A real file blocks creating the history directory (no mock helper).
        blocker = self.backup / "_deleted_history"
        blocker.write_bytes(b"do not overwrite")
        with patch("echo_vault.os.replace", wraps=os.replace) as replace:
            with self.assertLogs(level="INFO") as captured:
                self.modify()
        replace.assert_not_called()
        self.assert_no_sync_success(captured)
        self.assertEqual(self.backup_file.read_bytes(), previous)
        self.assertEqual(blocker.read_bytes(), b"do not overwrite")
        self.assert_no_staging()

    def test_retry_after_replace_failure_keeps_every_recoverable_snapshot(self):
        previous = self.backup_file.read_bytes()
        with patch("echo_vault.os.replace", side_effect=OSError("injected replacement failure")):
            with self.assertLogs(level="ERROR"):
                self.modify()
        self.assertEqual(self.backup_file.read_bytes(), previous)
        self.modify()
        self.assertEqual(self.backup_file.read_bytes(), self.source_file.read_bytes())
        self.assertEqual(self.history_contents(), {"notes.txt": previous, "notes_1.txt": previous})
        self.assert_no_staging()

    def test_success_preserves_old_and_new_metadata(self):
        previous = self.backup_file.read_bytes()
        self.backup_file.chmod(0o600)
        os.utime(self.backup_file, (1_500_000_000, 1_500_000_000))
        before = self.backup_file.stat()
        self.source_file.chmod(0o640)
        os.utime(self.source_file, (1_600_000_000, 1_600_000_000))
        self.modify()
        archived = self.history / "notes.txt"
        self.assertTrue(archived.exists(), "Previous backup must be archived")
        self.assertEqual(archived.read_bytes(), previous)
        self.assertEqual(archived.stat().st_mtime_ns, before.st_mtime_ns)
        self.assertEqual(stat.S_IMODE(archived.stat().st_mode), 0o600)
        self.assertEqual(self.backup_file.stat().st_mtime_ns, self.source_file.stat().st_mtime_ns)
        self.assertEqual(stat.S_IMODE(self.backup_file.stat().st_mode), 0o640)
        self.assertEqual(self.backup_file.read_bytes(), self.source_file.read_bytes())
        self.assert_no_staging()

    def test_ignored_directory_and_missing_source_events_do_not_change_backup(self):
        previous = self.backup_file.read_bytes()
        ignored = self.source / ".git" / "notes.txt"
        ignored.parent.mkdir()
        ignored.write_bytes(b"ignored bytes")
        self.modify(ignored)
        self.handler.dispatch(DirModifiedEvent(str(self.source)))
        self.source_file.unlink()
        self.modify()
        self.assertEqual(self.backup_file.read_bytes(), previous)
        self.assertEqual(list(self.backup.iterdir()), [self.backup_file])


if __name__ == "__main__":
    unittest.main()
