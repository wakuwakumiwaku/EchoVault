"""Archive setup failures through real watchdog delete/move dispatch, without watchers."""
from datetime import datetime
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from watchdog.events import FileCreatedEvent, FileDeletedEvent, FileMovedEvent
from watchdog.observers.api import BaseObserver, EventEmitter, ObservedWatch

from echo_vault import SafeBackupHandler


class ArchiveSetupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="echovault-archive-setup-")
        self.addCleanup(temporary.cleanup)
        self.source = Path(temporary.name) / "source"
        self.backup = Path(temporary.name) / "backup"
        self.source.mkdir()
        self.backup.mkdir()
        self.handler = SafeBackupHandler(str(self.source), str(self.backup))
        self.old_source = self.source / "notes.txt"
        self.active = self.backup / "notes.txt"
        self.active.write_bytes(b"previous active backup\x00\xff")
        self.active.chmod(0o640)
        os.utime(self.active, (1_500_000_000, 1_500_000_000))
        self.before = self.active.stat()
        self.previous = self.active.read_bytes()
        self.canary = self.backup / "unrelated.txt"
        self.canary.write_bytes(b"unrelated backup must survive")
        self.history_root = self.backup / "_deleted_history"
        self.history = self.history_root / "2026-01-02_03-04-05"
        clock = patch("echo_vault.datetime")
        clock.start().now.return_value = datetime(2026, 1, 2, 3, 4, 5)
        self.addCleanup(clock.stop)
        sleep = patch("echo_vault.time.sleep")
        sleep.start()
        self.addCleanup(sleep.stop)

    def event(self, moved=False):
        if moved:
            new_source = self.source / "renamed.txt"
            new_source.write_bytes(b"already synced renamed contents")
            (self.backup / new_source.name).write_bytes(new_source.read_bytes())
            return FileMovedEvent(str(self.old_source), str(new_source))
        return FileDeletedEvent(str(self.old_source))

    def assert_intact(self):
        self.assertEqual(self.active.read_bytes(), self.previous)
        self.assertEqual(self.active.stat().st_mtime_ns, self.before.st_mtime_ns)
        self.assertEqual(stat.S_IMODE(self.active.stat().st_mode), 0o640)
        self.assertEqual(self.canary.read_bytes(), b"unrelated backup must survive")
        self.assertEqual(list(self.backup.rglob(".echovault-*.tmp")), [])

    def assert_failed_archive(self, captured):
        self.assertTrue(any(record.levelno >= 40 and
                            "Failed to archive" in record.getMessage() and
                            str(self.active) in record.getMessage()
                            for record in captured.records))
        self.assertFalse(any(record.getMessage().startswith(("ARCHIVED:", "SYNCED:"))
                             for record in captured.records))
        self.assert_intact()

    def retry(self, event):
        with self.assertLogs(level="INFO") as captured:
            self.handler.dispatch(event)
        self.assertFalse(self.active.exists())
        self.assertEqual((self.history / "notes.txt").read_bytes(), self.previous)
        self.assertTrue(any(record.getMessage().startswith("ARCHIVED:")
                            for record in captured.records))
        # An ordinary later event still reaches sync on this same handler.
        later = self.source / "later.txt"
        later.write_bytes(b"later ordinary event")
        self.handler.dispatch(FileCreatedEvent(str(later)))
        self.assertEqual((self.backup / later.name).read_bytes(), later.read_bytes())

    def blocked_history(self, moved, timestamp=False):
        event = self.event(moved)
        older = self.history_root / "older" / "notes.txt"
        if timestamp:
            older.parent.mkdir(parents=True)
            older.write_bytes(b"older history canary")
        blocker = self.history if timestamp else self.history_root
        blocker.write_bytes(b"history directory blocker")
        with self.assertLogs(level="INFO") as captured:
            self.handler.dispatch(event)
        self.assert_failed_archive(captured)
        self.assertEqual(blocker.read_bytes(), b"history directory blocker")
        if timestamp:
            self.assertEqual(older.read_bytes(), b"older history canary")
        blocker.unlink()
        self.retry(event)
        if timestamp:
            self.assertEqual(older.read_bytes(), b"older history canary")

    def test_deleted_event_contains_real_blocked_history_root(self):
        self.blocked_history(False)

    def test_moved_event_contains_real_blocked_history_root(self):
        self.blocked_history(True)

    def test_deleted_event_contains_real_blocked_timestamp_directory(self):
        self.blocked_history(False, timestamp=True)

    def test_moved_event_contains_real_blocked_timestamp_directory(self):
        self.blocked_history(True, timestamp=True)

    def denied_mkdir(self, moved):
        event = self.event(moved)
        with patch("echo_vault.os.makedirs", side_effect=PermissionError("history denied")) as mkdir:
            with self.assertLogs(level="INFO") as captured:
                self.handler.dispatch(event)
        mkdir.assert_called_once_with(str(self.history), exist_ok=True)
        self.assert_failed_archive(captured)
        self.assertFalse(self.history_root.exists())
        self.retry(event)

    def test_deleted_event_contains_history_mkdir_permission_error(self):
        self.denied_mkdir(False)

    def test_moved_event_contains_history_mkdir_permission_error(self):
        self.denied_mkdir(True)

    def collision_error(self, moved):
        event = self.event(moved)
        self.history.mkdir(parents=True)
        existing = self.history / "notes.txt"
        existing.write_bytes(b"existing colliding history")
        with patch("echo_vault.os.path.lexists", side_effect=OSError("collision lookup denied")) as lookup:
            with self.assertLogs(level="INFO") as captured:
                self.handler.dispatch(event)
        lookup.assert_called_once_with(str(existing))
        self.assert_failed_archive(captured)
        self.assertEqual(existing.read_bytes(), b"existing colliding history")
        self.handler.dispatch(event)
        self.assertFalse(self.active.exists())
        self.assertEqual(existing.read_bytes(), b"existing colliding history")
        self.assertEqual((self.history / "notes_1.txt").read_bytes(), self.previous)

    def test_deleted_event_contains_collision_lookup_oserror(self):
        self.collision_error(False)

    def test_moved_event_contains_collision_lookup_oserror(self):
        self.collision_error(True)

    def test_helper_returns_false_without_cleaning_existing_history_on_setup_failure(self):
        self.history.mkdir(parents=True)
        existing = self.history / "notes.txt"
        existing.write_bytes(b"do not clean this existing history")
        for preserve in (False, True):
            with self.subTest(preserve_original=preserve):
                with patch("echo_vault.os.path.lexists", side_effect=OSError("lookup failed")):
                    with self.assertLogs(level="INFO") as captured:
                        result = self.handler._archive_specific_file(str(self.active), preserve_original=preserve)
                self.assertIs(result, False)
                self.assert_failed_archive(captured)
                self.assertEqual(existing.read_bytes(), b"do not clean this existing history")
        self.assertTrue(self.handler._archive_specific_file(str(self.active), preserve_original=True))
        self.assert_intact()
        self.assertEqual((self.history / "notes_1.txt").read_bytes(), self.previous)

    def test_move_keeps_truthful_new_file_sync_when_old_archive_setup_fails(self):
        new_source = self.source / "renamed.txt"
        new_source.write_bytes(b"genuinely new renamed contents")
        self.history_root.write_bytes(b"blocker")
        with self.assertLogs(level="INFO") as captured:
            self.handler.dispatch(FileMovedEvent(str(self.old_source), str(new_source)))
        self.assert_intact()
        self.assertEqual((self.backup / new_source.name).read_bytes(), new_source.read_bytes())
        messages = [record.getMessage() for record in captured.records]
        self.assertEqual([message for message in messages if message.startswith("SYNCED:")],
                         [f"SYNCED: {new_source} -> {self.backup / new_source.name}"])
        self.assertFalse(any(message.startswith("ARCHIVED:") for message in messages))
        self.assertTrue(any(message.startswith("Failed to archive") for message in messages))

    def test_actual_watchdog_queue_run_continues_after_failed_delete(self):
        self.history_root.write_bytes(b"blocker")
        later = self.source / "queued.txt"
        later.write_bytes(b"queued ordinary event")
        # Registration only: no schedule/start, emitter, filesystem monitor or
        # background thread. Execute the installed dispatch loop on this thread,
        # bounded to exactly two queue entries by its lifecycle predicate.
        observer = BaseObserver(EventEmitter)
        watch = ObservedWatch(str(self.source), recursive=False)
        observer.add_handler_for_watch(self.handler, watch)
        observer.event_queue.put((FileDeletedEvent(str(self.old_source)), watch))
        observer.event_queue.put((FileCreatedEvent(str(later)), watch))
        with patch.object(observer, "should_keep_running", side_effect=[True, True, False]):
            with self.assertLogs(level="INFO") as captured:
                observer.run()
        self.assert_intact()
        self.assertEqual(observer.event_queue.unfinished_tasks, 0)
        self.assertTrue(observer.event_queue.empty())
        self.assertEqual((self.backup / later.name).read_bytes(), later.read_bytes())
        self.assertTrue(any(record.getMessage().startswith("Failed to archive")
                            for record in captured.records))
        self.assertFalse(any(record.getMessage().startswith("ARCHIVED:")
                             for record in captured.records))
        self.history_root.unlink()
        self.retry(FileDeletedEvent(str(self.old_source)))

    def test_setup_does_not_swallow_baseexception(self):
        with patch("echo_vault.os.makedirs", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.handler.dispatch(FileDeletedEvent(str(self.old_source)))
        self.assert_intact()


if __name__ == "__main__":
    unittest.main()
