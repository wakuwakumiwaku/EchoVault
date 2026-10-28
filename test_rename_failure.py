"""Failed rename syncs must not remove the only active fallback backup."""
from datetime import datetime
import os
from pathlib import Path
import shutil
import stat
import tempfile
import unittest
from unittest.mock import patch

from watchdog.events import FileCreatedEvent, FileMovedEvent
from watchdog.observers.api import BaseObserver, EventEmitter, ObservedWatch

from echo_vault import SafeBackupHandler


class RenameFailureTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="echovault-rename-test-")
        self.addCleanup(temporary.cleanup)
        self.source = Path(temporary.name) / "source"
        self.backup = Path(temporary.name) / "backup"
        self.source.mkdir()
        self.backup.mkdir()
        self.old_source = self.source / "notes.txt"
        self.new_source = self.source / "renamed.txt"
        self.old_backup = self.backup / "notes.txt"
        self.new_backup = self.backup / "renamed.txt"
        self.previous = b"previous active backup\x00\xff"
        self.new_bytes = b"renamed source contents\x00\xfe"
        self.old_backup.write_bytes(self.previous)
        self.old_backup.chmod(0o640)
        os.utime(self.old_backup, (1_500_000_000, 1_500_000_000))
        self.before = self.old_backup.stat()
        self.old_source.write_bytes(self.new_bytes)
        self.old_source.rename(self.new_source)
        self.new_source.chmod(0o600)
        os.utime(self.new_source, (1_600_000_000, 1_600_000_000))
        self.handler = SafeBackupHandler(str(self.source), str(self.backup))
        self.event = FileMovedEvent(str(self.old_source), str(self.new_source))
        self.history = self.backup / "_deleted_history" / "2026-01-02_03-04-05"
        self.history.mkdir(parents=True)
        self.canaries = {}
        for path, contents in (
            (self.source / "untouched.txt", b"unrelated source"),
            (self.backup / "untouched.txt", b"unrelated active backup"),
            (self.history / "notes.txt", b"colliding history must survive"),
            (self.history.parent / "older" / "notes.txt", b"older history must survive"),
        ):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(contents)
            self.canaries[path] = (contents, path.stat().st_mtime_ns,
                                   stat.S_IMODE(path.stat().st_mode))
        self.addCleanup(self.assert_canaries)
        sleep = patch("echo_vault.time.sleep")
        sleep.start()
        self.addCleanup(sleep.stop)
        watcher = patch("watchdog.observers.api.BaseObserver.start",
                        side_effect=AssertionError("live watchers are forbidden"))
        watcher.start()
        self.addCleanup(watcher.stop)
        clock = patch("echo_vault.datetime")
        clock.start().now.return_value = datetime(2026, 1, 2, 3, 4, 5)
        self.addCleanup(clock.stop)

    def assert_canaries(self):
        for path, (contents, mtime, mode) in self.canaries.items():
            self.assertEqual(path.read_bytes(), contents)
            self.assertEqual(path.stat().st_mtime_ns, mtime)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), mode)

    def dispatch_move(self):
        self.handler.dispatch(self.event)

    def assert_old_intact(self):
        self.assertTrue(self.old_backup.exists(),
                        "Failed new-path sync must retain the old active backup")
        self.assertEqual(self.old_backup.read_bytes(), self.previous)
        self.assertEqual(self.old_backup.stat().st_mtime_ns, self.before.st_mtime_ns)
        self.assertEqual(stat.S_IMODE(self.old_backup.stat().st_mode), 0o640)

    def assert_no_staging(self):
        self.assertEqual(list(self.backup.rglob(".echovault-*.tmp")), [])

    def history_contents(self):
        return {path.relative_to(self.history.parent).as_posix(): path.read_bytes()
                for path in self.history.parent.rglob("*") if path.is_file()}

    def assert_deferred(self, captured):
        self.assert_old_intact()
        self.assert_no_staging()
        messages = [record.getMessage() for record in captured.records]
        self.assertFalse(any(message.startswith(f"ARCHIVED: {self.old_backup} ->")
                             for message in messages))
        self.assertFalse(any(message.startswith("SYNCED:") for message in messages))
        self.assertTrue(any(record.levelno >= 30 and "defer" in record.getMessage().lower()
                            and str(self.old_source) in record.getMessage()
                            for record in captured.records))

    def complete_move_and_replay(self):
        self.dispatch_move()
        self.assertFalse(self.old_backup.exists())
        self.assertEqual(self.new_backup.read_bytes(), self.new_source.read_bytes())
        archived = self.history / "notes_1.txt"
        self.assertTrue(archived.exists(), "Old backup must be archived after successful sync")
        self.assertEqual(archived.read_bytes(), self.previous)
        self.assertEqual(archived.stat().st_mtime_ns, self.before.st_mtime_ns)
        self.assertEqual(stat.S_IMODE(archived.stat().st_mode), 0o640)
        history = self.history_contents()
        self.dispatch_move()  # Duplicate event is a no-op after the rename completes.
        self.assertEqual(self.history_contents(), history)
        later = self.source / "later.txt"
        later.write_bytes(b"later ordinary event")
        self.handler.dispatch(FileCreatedEvent(str(later)))
        self.assertEqual((self.backup / later.name).read_bytes(), later.read_bytes())
        self.assert_no_staging()
        self.assert_canaries()

    def fail_then_retry(self, target, failure, existing_new=None):
        if existing_new is not None:
            self.new_backup.write_bytes(existing_new)
        with patch(target, side_effect=failure) as boundary:
            with self.assertLogs(level="INFO") as captured:
                self.dispatch_move()
        boundary.assert_called()
        self.assert_deferred(captured)
        if existing_new is None:
            self.assertFalse(self.new_backup.exists())
        else:
            self.assertEqual(self.new_backup.read_bytes(), existing_new)
        self.assertEqual(self.new_source.read_bytes(), self.new_bytes)
        self.complete_move_and_replay()

    def test_partial_source_copy_failure_keeps_old_active_until_successful_retry(self):
        before = self.history_contents()

        def interrupted_copy(source, destination):
            self.assertEqual(Path(source), self.new_source)
            Path(destination).write_bytes(b"partial source copy")
            raise OSError("injected renamed-source copy failure")

        self.fail_then_retry("echo_vault.shutil.copy2", interrupted_copy)
        self.assertEqual(self.history_contents(), {
            **before, "2026-01-02_03-04-05/notes_1.txt": self.previous,
        })

    def test_destination_directory_failure_keeps_old_active_until_retry(self):
        real_makedirs = os.makedirs

        def denied_destination(path, *args, **kwargs):
            if Path(path) == self.backup:
                raise PermissionError("injected destination directory failure")
            return real_makedirs(path, *args, **kwargs)

        self.fail_then_retry("echo_vault.os.makedirs", denied_destination)

    def test_staging_creation_failure_keeps_old_active_until_retry(self):
        self.fail_then_retry("echo_vault.tempfile.mkstemp",
                             OSError("injected staging allocation failure"))

    def test_first_publication_failure_keeps_old_active_until_retry(self):
        self.fail_then_retry("echo_vault.os.replace",
                             OSError("injected renamed-file publication failure"))

    def test_occupied_new_path_replacement_failure_preserves_both_active_backups(self):
        occupied = b"previous backup at the new path"
        self.fail_then_retry("echo_vault.os.replace", OSError("injected replace failure"),
                             existing_new=occupied)
        self.assertEqual((self.history / "renamed.txt").read_bytes(), occupied)
        self.assertEqual((self.history / "renamed_1.txt").read_bytes(), occupied)

    def test_occupied_new_path_snapshot_failure_does_not_archive_old_fallback(self):
        occupied = b"previous backup at the new path"
        real_copy = shutil.copy2

        def interrupted_snapshot(source, destination):
            if Path(source) == self.new_backup:
                Path(destination).write_bytes(b"partial history copy")
                raise OSError("injected new-path history failure")
            return real_copy(source, destination)

        self.fail_then_retry("echo_vault.shutil.copy2", interrupted_snapshot,
                             existing_new=occupied)
        self.assertEqual((self.history / "renamed.txt").read_bytes(), occupied)
        self.assertFalse((self.history / "renamed_1.txt").exists())

    def test_missing_new_source_keeps_old_fallback_and_can_be_retried(self):
        self.new_source.unlink()
        history = self.history_contents()
        with self.assertLogs(level="INFO") as captured:
            self.dispatch_move()
        self.assert_deferred(captured)
        self.assertFalse(self.new_backup.exists())
        self.assertEqual(self.history_contents(), history)
        self.new_source.write_bytes(self.new_bytes)
        self.complete_move_and_replay()

    def test_successful_rename_publishes_new_before_archiving_old_with_metadata(self):
        real_move = shutil.move

        def archive_after_publication(source, destination):
            self.assertEqual(Path(source), self.old_backup)
            self.assert_old_intact()
            self.assertEqual(self.new_backup.read_bytes(), self.new_bytes)
            self.assertEqual(self.new_backup.stat().st_mtime_ns,
                             self.new_source.stat().st_mtime_ns)
            self.assertEqual(stat.S_IMODE(self.new_backup.stat().st_mode), 0o600)
            return real_move(source, destination)

        with patch("echo_vault.shutil.move", side_effect=archive_after_publication) as move:
            self.complete_move_and_replay()
        move.assert_called_once()

    def test_identical_new_backup_is_success_without_redundant_copy(self):
        self.new_backup.write_bytes(self.new_bytes)
        with patch("echo_vault.shutil.copy2", wraps=shutil.copy2) as copy:
            self.dispatch_move()
        copy.assert_not_called()
        self.complete_move_and_replay()

    def test_successful_rename_over_occupied_new_path_keeps_both_previous_versions(self):
        self.new_backup.write_bytes(b"occupied target backup")
        self.complete_move_and_replay()
        self.assertEqual((self.history / "renamed.txt").read_bytes(), b"occupied target backup")

    def test_absent_old_backup_still_syncs_new_without_archiving(self):
        self.old_backup.unlink()
        history = self.history_contents()
        self.dispatch_move()
        self.assertEqual(self.new_backup.read_bytes(), self.new_bytes)
        self.assertEqual(self.history_contents(), history)
        self.assert_no_staging()

    def test_old_archive_failure_preserves_both_active_paths_then_retries(self):
        with patch("echo_vault.shutil.move", side_effect=OSError("injected old archive failure")):
            with self.assertLogs(level="INFO") as captured:
                self.dispatch_move()
        self.assert_old_intact()
        self.assertEqual(self.new_backup.read_bytes(), self.new_bytes)
        self.assertTrue(any(record.getMessage().startswith("SYNCED:")
                            for record in captured.records))
        self.assertTrue(any(record.getMessage().startswith("Failed to archive")
                            for record in captured.records))
        self.complete_move_and_replay()

    def test_actual_watchdog_queue_continues_after_failed_move_then_retries(self):
        later = self.source / "queued.txt"
        later.write_bytes(b"queued subsequent event")
        real_copy = shutil.copy2

        def fail_only_rename(source, destination):
            if Path(source) == self.new_source:
                Path(destination).write_bytes(b"partial")
                raise OSError("injected queued rename copy failure")
            return real_copy(source, destination)

        # No schedule/start, emitter, thread or filesystem watcher: consume the
        # installed watchdog dispatch loop synchronously, bounded to two events.
        observer = BaseObserver(EventEmitter)
        watch = ObservedWatch(str(self.source), recursive=False)
        observer.add_handler_for_watch(self.handler, watch)
        observer.event_queue.put((self.event, watch))
        observer.event_queue.put((FileCreatedEvent(str(later)), watch))
        with patch("echo_vault.shutil.copy2", side_effect=fail_only_rename):
            with patch.object(observer, "should_keep_running", side_effect=[True, True, False]):
                with self.assertLogs(level="INFO"):
                    observer.run()
        self.assert_old_intact()
        self.assertEqual((self.backup / later.name).read_bytes(), later.read_bytes())
        self.assertEqual(observer.event_queue.unfinished_tasks, 0)
        self.assertTrue(observer.event_queue.empty())
        self.assert_no_staging()
        self.complete_move_and_replay()

    def test_keyboard_interrupt_propagates_without_archiving_old_backup(self):
        with patch("echo_vault.shutil.copy2", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.dispatch_move()
        self.assert_old_intact()
        self.assert_no_staging()
        self.complete_move_and_replay()


if __name__ == "__main__":
    unittest.main()
