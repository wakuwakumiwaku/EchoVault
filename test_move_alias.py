"""Move dispatch must not retire the newly current backup through an alias."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from watchdog.events import FileMovedEvent
import echo_vault
from echo_vault import SafeBackupHandler

assert Path(echo_vault.__file__).resolve() == Path(__file__).resolve().with_name("echo_vault.py")


class MoveAliasTests(unittest.TestCase):
    def exercise(self, alias, changed=False):
        with tempfile.TemporaryDirectory(prefix="echo-move-alias-") as temp, \
                patch("watchdog.observers.api.BaseObserver.start", side_effect=AssertionError("no watchers")):
            root = Path(temp)
            source, backup = root / "source", root / "backup"
            source.mkdir()
            backup.mkdir()
            (source / "before").mkdir()
            (source / "after").mkdir()
            old = source / "before" / "notes.txt"
            new = source / "after" / "notes.txt"
            old.write_bytes(b"old contents")
            handler = SafeBackupHandler(str(source), str(backup))
            self.assertTrue(handler.sync_file(str(old)))
            if alias:
                (backup / "after").symlink_to(backup / "before", target_is_directory=True)
            old.rename(new)
            if changed:
                new.write_bytes(b"new contents")
            handler.dispatch(FileMovedEvent(str(old), str(new)))
            active = backup / "after" / "notes.txt"
            self.assertTrue(active.is_file(), "move must retain the active new backup, even through an alias")
            self.assertEqual(active.read_bytes(), new.read_bytes())
            history = [p.read_bytes() for p in (backup / "_deleted_history").rglob("*") if p.is_file()]
            self.assertEqual(history, [b"old contents"] if changed or not alias else [])
            if not alias:
                self.assertFalse((backup / "before" / "notes.txt").exists())
            self.assertEqual(list(backup.rglob(".echovault-*.tmp")), [])

    def test_same_destination_alias_keeps_unchanged_active_backup(self):
        self.exercise(alias=True)

    def test_same_destination_alias_keeps_updated_active_and_old_history(self):
        self.exercise(alias=True, changed=True)

    def test_distinct_destinations_still_archive_old_backup(self):
        self.exercise(alias=False)


if __name__ == "__main__":
    unittest.main()
