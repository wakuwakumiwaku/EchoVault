"""File moves must respect both endpoints of the ignored-directory boundary."""
from datetime import datetime
import logging
import os
from pathlib import Path
import shutil
import stat
import tempfile
import unittest
from unittest.mock import patch

from watchdog.events import DirMovedEvent, FileCreatedEvent, FileMovedEvent
from watchdog.observers.api import BaseObserver, EventEmitter, ObservedWatch

from echo_vault import SafeBackupHandler


class MoveIgnoreBoundaryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="echovault-ignore-move-")
        self.addCleanup(temporary.cleanup)
        self.source = Path(temporary.name) / "source"
        self.backup = Path(temporary.name) / "backup"
        self.source.mkdir()
        self.backup.mkdir()
        self.handler = SafeBackupHandler(str(self.source), str(self.backup))
        self.previous = b"previous included backup\x00\xff"
        self.current = b"newly moved source\x00\xfe"
        self.history = self.backup / "_deleted_history" / "2026-01-02_03-04-05"
        self.canaries = {}
        for path, contents in (
            (self.source / "untouched.bin", b"unrelated source"),
            (self.backup / "untouched.bin", b"unrelated active backup"),
            (self.history / "notes.txt", b"colliding historical backup"),
            (self.history.parent / "older" / "notes.txt", b"older historical backup"),
        ):
            self.add_canary(path, contents)
        self.addCleanup(self.assert_canaries)
        for target, options in (
            ("echo_vault.time.sleep", {}),
            ("echo_vault.datetime", {}),
            ("watchdog.observers.api.BaseObserver.start", {
                "side_effect": AssertionError("live watchers are forbidden")}),
        ):
            mocked = patch(target, **options)
            active = mocked.start()
            self.addCleanup(mocked.stop)
            if target == "echo_vault.datetime":
                active.now.return_value = datetime(2026, 1, 2, 3, 4, 5)
        self.records = []
        capture = logging.Handler()
        capture.emit = self.records.append
        root = logging.getLogger()
        previous_level = root.level
        root.setLevel(logging.INFO)
        root.addHandler(capture)
        self.addCleanup(root.setLevel, previous_level)
        self.addCleanup(root.removeHandler, capture)

    def add_canary(self, path, contents):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contents)
        path.chmod(0o640)
        os.utime(path, (1_400_000_000, 1_400_000_000))
        self.canaries[path] = self.file_state(path)

    def file_state(self, path):
        details = path.stat()
        return path.read_bytes(), details.st_mtime_ns, stat.S_IMODE(details.st_mode)

    def assert_canaries(self):
        for path, state in self.canaries.items():
            self.assertTrue(path.is_file(), "Move must retain unrelated/ignored backup canaries")
            self.assertEqual(self.file_state(path), state)

    def prepare_move(self, old_relative, new_relative, old_ignored=False):
        self.old_source = self.source / old_relative
        self.new_source = self.source / new_relative
        self.old_backup = self.backup / old_relative
        self.new_backup = self.backup / new_relative
        self.old_source.parent.mkdir(parents=True, exist_ok=True)
        self.new_source.parent.mkdir(parents=True, exist_ok=True)
        self.old_source.write_bytes(self.current)
        self.old_source.chmod(0o600)
        os.utime(self.old_source, (1_600_000_000, 1_600_000_000))
        self.old_source.rename(self.new_source)
        self.old_backup.parent.mkdir(parents=True, exist_ok=True)
        self.old_backup.write_bytes(self.previous)
        self.old_backup.chmod(0o640)
        os.utime(self.old_backup, (1_500_000_000, 1_500_000_000))
        self.old_state = self.file_state(self.old_backup)
        if old_ignored:
            self.canaries[self.old_backup] = self.old_state
        self.event = FileMovedEvent(str(self.old_source), str(self.new_source))

    def dispatch(self):
        self.handler.dispatch(self.event)

    def history_contents(self):
        return {path.relative_to(self.history.parent).as_posix(): path.read_bytes()
                for path in self.history.parent.rglob("*") if path.is_file()}

    def assert_current(self):
        self.assertTrue(self.new_backup.is_file(),
                        "A file moved out of an ignored directory must enter the active backup")
        self.assertEqual(self.file_state(self.new_backup), self.file_state(self.new_source))

    def assert_old_intact(self):
        self.assertTrue(self.old_backup.is_file(), "Archive failure must retain the old active backup")
        self.assertEqual(self.file_state(self.old_backup), self.old_state)

    def assert_archived(self):
        self.assertFalse(self.old_backup.exists(),
                         "Moving into an ignored directory must retire the old included backup")
        archived = self.history / "notes_1.txt"
        self.assertTrue(archived.is_file(), "The retired backup must be safely archived")
        self.assertEqual(self.file_state(archived), self.old_state)

    def assert_no_staging(self):
        self.assertEqual(list(self.backup.rglob(".echovault-*.tmp")), [])

    def replay_and_continue(self):
        history = self.history_contents()
        self.dispatch()
        self.assertEqual(self.history_contents(), history, "Duplicate replay must not add history")
        later = self.source / "later.bin"
        later.write_bytes(b"ordinary later event")
        self.handler.dispatch(FileCreatedEvent(str(later)))
        self.assertEqual((self.backup / later.name).read_bytes(), later.read_bytes())
        self.assert_no_staging()
        self.assert_canaries()

    def test_move_out_of_ignored_directory_syncs_new_file_without_archiving_ignored_backup(self):
        self.prepare_move(".git/notes.txt", "visible/notes.txt", old_ignored=True)
        before = self.history_contents()
        self.dispatch()
        self.assert_current()
        self.assertEqual(self.history_contents(), before)
        self.replay_and_continue()

    def test_move_out_of_nested_ignored_directory_preserves_occupied_target_history(self):
        self.prepare_move("project/__pycache__/notes.txt", "visible/notes.txt", old_ignored=True)
        self.new_backup.parent.mkdir()
        self.new_backup.write_bytes(b"previous target backup")
        self.new_backup.chmod(0o640)
        os.utime(self.new_backup, (1_450_000_000, 1_450_000_000))
        previous_target = self.file_state(self.new_backup)
        self.dispatch()
        self.assert_current()
        self.assertEqual(self.file_state(self.history / "notes_1.txt"), previous_target)
        self.replay_and_continue()

    def test_move_into_ignored_directory_archives_old_without_copying_excluded_source(self):
        self.prepare_move("visible/notes.txt", ".git/notes.txt")
        with patch("echo_vault.shutil.copy2", wraps=shutil.copy2) as copy:
            self.dispatch()
        self.assertFalse(self.new_backup.exists(), "Ignored move target must not be backed up")
        copy.assert_not_called()
        self.assert_archived()
        self.assertEqual(self.new_source.read_bytes(), self.current)
        self.replay_and_continue()

    def test_move_into_nested_ignored_directory_leaves_existing_ignored_backup_untouched(self):
        self.prepare_move("visible/notes.txt", "project/.idea/notes.txt")
        self.add_canary(self.new_backup, b"existing ignored backup must not be replaced")
        self.dispatch()
        self.assert_canaries()
        self.assert_archived()
        self.replay_and_continue()

    def test_move_between_ignored_directories_is_noop(self):
        self.prepare_move(".git/notes.txt", ".idea/notes.txt", old_ignored=True)
        self.add_canary(self.new_backup, b"another ignored backup")
        before = self.history_contents()
        with patch("echo_vault.shutil.copy2", wraps=shutil.copy2) as copy:
            with patch("echo_vault.shutil.move", wraps=shutil.move) as move:
                self.dispatch()
        copy.assert_not_called()
        move.assert_not_called()
        self.assertEqual(self.history_contents(), before)
        self.assert_canaries()
        self.assert_no_staging()

    def test_ordinary_rename_still_syncs_before_retiring_old_backup(self):
        self.prepare_move("visible/notes.txt", "other/renamed.txt")
        real_move = shutil.move

        def archive_after_sync(source, destination):
            self.assert_current()
            self.assert_old_intact()
            return real_move(source, destination)

        with patch("echo_vault.shutil.move", side_effect=archive_after_sync) as move:
            self.dispatch()
        move.assert_called_once()
        self.assert_current()
        self.assert_archived()
        self.replay_and_continue()

    def test_directory_move_remains_noop(self):
        self.prepare_move("visible/notes.txt", ".git/notes.txt")
        self.event = DirMovedEvent(str(self.old_source.parent), str(self.new_source.parent))
        before = self.history_contents()
        self.dispatch()
        self.assert_old_intact()
        self.assertFalse(self.new_backup.exists())
        self.assertEqual(self.history_contents(), before)
        self.assert_no_staging()

    def test_move_out_copy_failure_cleans_partial_stage_then_same_event_recovers(self):
        self.prepare_move(".git/notes.txt", "visible/notes.txt", old_ignored=True)
        before = self.history_contents()

        def interrupted_copy(source, destination):
            self.assertEqual(Path(source), self.new_source)
            Path(destination).write_bytes(b"partial")
            raise OSError("injected move-out source copy failure")

        with patch("echo_vault.shutil.copy2", side_effect=interrupted_copy) as copy:
            self.dispatch()
        self.assertFalse(self.new_backup.exists())
        self.assertEqual(self.history_contents(), before)
        self.assert_old_intact()
        self.assert_no_staging()
        self.assertFalse(any(record.getMessage().startswith("SYNCED:") for record in self.records))
        self.dispatch()
        self.assert_current()
        copy.assert_called_once()
        self.assertTrue(any("injected move-out source copy failure" in record.getMessage()
                            for record in self.records))
        self.replay_and_continue()

    def test_move_out_replace_failure_keeps_occupied_target_and_recovers(self):
        self.prepare_move(".git/notes.txt", "visible/notes.txt", old_ignored=True)
        self.new_backup.parent.mkdir()
        self.new_backup.write_bytes(b"previous target backup")
        previous_target = self.file_state(self.new_backup)
        with patch("echo_vault.os.replace", side_effect=OSError("injected move-out replace failure")) as replace:
            self.dispatch()
        self.assertEqual(self.file_state(self.new_backup), previous_target)
        self.assert_old_intact()
        self.assert_no_staging()
        self.assertFalse(any(record.getMessage().startswith("SYNCED:") for record in self.records))
        self.dispatch()
        self.assert_current()
        replace.assert_called_once()
        self.assertEqual(self.file_state(self.history / "notes_1.txt"), previous_target)
        self.assertEqual(self.file_state(self.history / "notes_2.txt"), previous_target)
        self.replay_and_continue()

    def test_move_into_ignored_directory_archive_setup_failure_retains_old_then_recovers(self):
        self.prepare_move("visible/notes.txt", ".git/notes.txt")
        saved_history = self.history.with_name("saved-history")
        self.history.rename(saved_history)
        self.history.write_bytes(b"not a history directory")
        try:
            self.dispatch()
            self.assertEqual(self.history.read_bytes(), b"not a history directory")
        finally:
            self.history.unlink()
            saved_history.rename(self.history)
        self.assert_old_intact()
        self.assertFalse(self.new_backup.exists(), "Archive failure must not sync an ignored target")
        self.assertFalse(any(record.getMessage().startswith(("SYNCED:", "ARCHIVED:"))
                             for record in self.records))
        self.dispatch()
        self.assert_archived()
        self.assertTrue(any(record.getMessage().startswith("Failed to archive")
                            for record in self.records))
        self.replay_and_continue()

    def test_move_into_ignored_directory_archive_move_failure_retains_old_then_recovers(self):
        self.prepare_move("visible/notes.txt", ".git/notes.txt")
        before = self.history_contents()
        with patch("echo_vault.shutil.move", side_effect=PermissionError("injected archive move failure")) as move:
            self.dispatch()
        self.assert_old_intact()
        self.assertFalse(self.new_backup.exists(), "Archive failure must not sync an ignored target")
        self.assertEqual(self.history_contents(), before)
        self.assert_no_staging()
        self.assertFalse(any(record.getMessage().startswith(("SYNCED:", "ARCHIVED:"))
                             for record in self.records))
        move.assert_called_once()
        self.dispatch()
        self.assert_archived()
        self.replay_and_continue()

    def test_actual_watchdog_queue_syncs_move_out_and_later_event_without_starting_watcher(self):
        self.prepare_move(".git/notes.txt", "visible/notes.txt", old_ignored=True)
        later = self.source / "queued.bin"
        later.write_bytes(b"queued ordinary event")
        observer = BaseObserver(EventEmitter)
        watch = ObservedWatch(str(self.source), recursive=True)
        observer.add_handler_for_watch(self.handler, watch)
        observer.event_queue.put((self.event, watch))
        observer.event_queue.put((FileCreatedEvent(str(later)), watch))
        with patch.object(observer, "should_keep_running", side_effect=[True, True, False]):
            observer.run()
        self.assertTrue(observer.event_queue.empty())
        self.assertEqual(observer.event_queue.unfinished_tasks, 0)
        self.assertEqual((self.backup / later.name).read_bytes(), later.read_bytes())
        self.assert_current()
        self.replay_and_continue()


if __name__ == "__main__":
    unittest.main()
