"""Reject root mappings that can copy backups recursively or overwrite sources."""
from contextlib import ExitStack
import os
from pathlib import Path
import shutil
import stat
import tempfile
import unittest
from unittest.mock import patch

from watchdog.events import FileCreatedEvent, FileModifiedEvent

from echo_vault import SafeBackupHandler, run_initial_sync


class RootOverlapTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="echovault-roots-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "source"
        self.backup = self.root / "backup"
        self.source.mkdir()
        self.backup.mkdir()
        for path, contents in (
            (self.source / "notes.bin", b"original source\x00\xff"),
            (self.backup / "notes.bin", b"existing backup\x00\xfe"),
            (self.backup / "_deleted_history" / "older" / "notes.bin", b"older history"),
        ):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(contents)
            path.chmod(0o640)
            os.utime(path, (1_500_000_000, 1_500_000_000))
        watcher = patch("watchdog.observers.api.BaseObserver.start",
                        side_effect=AssertionError("live watchers are forbidden"))
        watcher.start()
        self.addCleanup(watcher.stop)
        sleep = patch("echo_vault.time.sleep")
        sleep.start()
        self.addCleanup(sleep.stop)

    def tree_state(self):
        result = {}
        for path in self.root.rglob("*"):
            details = path.lstat()
            if path.is_symlink():
                contents = ("link", os.readlink(path))
            elif path.is_file():
                contents = ("file", path.read_bytes())
            else:
                contents = ("directory",)
            result[path.relative_to(self.root).as_posix()] = (
                contents, details.st_mtime_ns, stat.S_IMODE(details.st_mode))
        return result

    def assert_rejected(self, source, destination):
        before = self.tree_state()
        with ExitStack() as stack:
            boundaries = [stack.enter_context(patch(name, wraps=original)) for name, original in (
                ("echo_vault.os.makedirs", os.makedirs),
                ("echo_vault.shutil.copy2", shutil.copy2),
                ("echo_vault.os.replace", os.replace),
                ("echo_vault.shutil.move", shutil.move),
            )]
            with self.assertRaisesRegex(ValueError, "(?i)(overlap|disjoint)"):
                SafeBackupHandler(source, destination)
            for boundary in boundaries:
                boundary.assert_not_called()
        self.assertEqual(self.tree_state(), before,
                         "Invalid backup roots must be refused without changing any files")

    def test_equal_roots_are_rejected_without_writing(self):
        self.assert_rejected(str(self.source), str(self.source))

    def test_backup_inside_source_is_rejected(self):
        nested = self.source / "backups"
        nested.mkdir()
        (nested / "preserve.bin").write_bytes(b"existing nested backup")
        self.assert_rejected(str(self.source), str(nested))

    def test_not_yet_created_backup_inside_source_is_rejected(self):
        self.assert_rejected(str(self.source), str(self.source / "new" / "backup"))
        self.assertFalse((self.source / "new").exists())

    def test_source_inside_backup_is_rejected_before_original_can_be_overwritten(self):
        # With destination=self.root, this nested file maps onto source/notes.bin.
        nested = self.source / "source" / "notes.bin"
        nested.parent.mkdir()
        nested.write_bytes(b"nested contents must never overwrite the original source")
        self.assert_rejected(str(self.source), str(self.root))

    def test_dot_components_cannot_hide_overlap(self):
        source = os.path.join(str(self.source), ".")
        destination = os.path.join(str(self.backup), "..", "source", "child")
        self.assert_rejected(source, destination)

    def test_relative_roots_cannot_hide_overlap(self):
        source = os.path.relpath(self.source)
        destination = os.path.relpath(self.source / "nested-backup")
        self.assert_rejected(source, destination)

    def test_pathlike_roots_are_checked(self):
        self.assert_rejected(self.source, self.source / "backup")

    def make_alias(self, target):
        alias = self.root / "source-alias"
        try:
            alias.symlink_to(target, target_is_directory=True)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"Directory symlink creation unavailable: {error}")
        return alias

    def test_alias_of_same_root_is_rejected(self):
        alias = self.make_alias(self.source)
        self.assert_rejected(str(self.source), str(alias))

    def test_backup_below_source_alias_is_rejected(self):
        alias = self.make_alias(self.source)
        self.assert_rejected(str(self.source), str(alias / "new-backup"))

    def test_aliased_source_inside_backup_is_rejected(self):
        alias = self.make_alias(self.source)
        self.assert_rejected(str(alias), str(self.root))

    def test_prefix_siblings_remain_valid_through_initial_sync_and_events(self):
        destination = self.root / "source-backup"
        source_arg = str(self.source)
        destination_arg = str(destination)
        handler = SafeBackupHandler(source_arg, destination_arg)
        self.assertEqual(handler.source_root, source_arg)
        self.assertEqual(handler.dest_root, destination_arg)
        protected = self.tree_state()
        source = self.source / "notes.bin"
        original = source.read_bytes()
        run_initial_sync(source_arg, destination_arg, handler)
        copied = destination / source.name
        self.assertEqual(copied.read_bytes(), original)
        self.assertEqual(copied.stat().st_mtime_ns, source.stat().st_mtime_ns)
        self.assertEqual(stat.S_IMODE(copied.stat().st_mode), stat.S_IMODE(source.stat().st_mode))
        self.assertFalse((destination / "_deleted_history").exists())
        source.write_bytes(b"changed through the actual modified-event caller")
        handler.dispatch(FileModifiedEvent(str(source)))
        self.assertEqual(copied.read_bytes(), source.read_bytes())
        historical = list((destination / "_deleted_history").glob("*/notes.bin"))
        self.assertEqual(len(historical), 1)
        self.assertEqual(historical[0].read_bytes(), original)
        later = self.source / "later.bin"
        later.write_bytes(b"later ordinary event")
        handler.dispatch(FileCreatedEvent(str(later)))
        self.assertEqual((destination / later.name).read_bytes(), later.read_bytes())
        self.assertFalse((destination / destination.name).exists())
        self.assertEqual(list(destination.rglob(".echovault-*.tmp")), [])
        after = self.tree_state()
        for path, state in protected.items():
            if path.startswith("backup/") or path == "backup":
                self.assertEqual(after[path], state)

    def test_disjoint_nonexistent_destination_and_pathlike_roots_remain_valid(self):
        destination = self.root / "new-parent" / "backup"
        handler = SafeBackupHandler(self.source, destination)
        self.assertIs(handler.source_root, self.source)
        self.assertIs(handler.dest_root, destination)
        self.assertFalse(destination.exists(), "Validation itself must not create directories")
        run_initial_sync(self.source, destination, handler)
        self.assertEqual((destination / "notes.bin").read_bytes(),
                         (self.source / "notes.bin").read_bytes())


if __name__ == "__main__":
    unittest.main()
