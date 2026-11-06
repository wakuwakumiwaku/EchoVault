"""Initial scans and event filtering must agree for ignored regular files."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import echo_vault as vault
from watchdog.events import FileModifiedEvent

assert Path(vault.__file__).resolve() == Path(__file__).resolve().parent / "echo_vault.py"


class InitialIgnoreFilesTests(unittest.TestCase):
    def test_ignored_regular_files_are_not_copied_at_any_depth(self):
        with tempfile.TemporaryDirectory() as temp:
            source, backup = Path(temp) / "source", Path(temp) / "backup"
            source.mkdir()
            backup.mkdir()
            for prefix in (Path(), Path("nested")):
                for name in vault.IGNORE_DIRS:
                    path = source / prefix / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(b"synthetic ignored metadata")
            (source / "nested" / "notes.txt").write_bytes(b"included")
            handler = vault.SafeBackupHandler(str(source), str(backup))
            with patch.object(vault.Observer, "start", side_effect=AssertionError("no watcher")):
                vault.run_initial_sync(str(source), str(backup), handler)
            copied = {p.relative_to(backup).as_posix() for p in backup.rglob("*") if p.is_file()}
            self.assertEqual(copied, {"nested/notes.txt"},
                             "initial scan must not copy names ignored by event dispatch")

    def test_existing_ignored_backup_is_not_replaced_or_archived(self):
        with tempfile.TemporaryDirectory() as temp:
            source, backup = Path(temp) / "source", Path(temp) / "backup"
            source.mkdir()
            backup.mkdir()
            (source / ".git").write_bytes(b"gitdir: synthetic-worktree-metadata")
            (backup / ".git").write_bytes(b"existing ignored backup")
            handler = vault.SafeBackupHandler(str(source), str(backup))
            vault.run_initial_sync(str(source), str(backup), handler)
            handler.dispatch(FileModifiedEvent(str(source / ".git")))
            self.assertEqual((backup / ".git").read_bytes(), b"existing ignored backup",
                             "startup must honor the same exclusion as later file events")
            self.assertFalse((backup / "_deleted_history").exists())

    def test_similarly_named_files_and_ordinary_hidden_files_remain_included(self):
        with tempfile.TemporaryDirectory() as temp:
            source, backup = Path(temp) / "source", Path(temp) / "backup"
            source.mkdir()
            backup.mkdir()
            names = [".gitignore", "__pycache__.txt", ".notes", "notes.txt"]
            for name in names:
                (source / name).write_text(name)
            handler = vault.SafeBackupHandler(str(source), str(backup))
            vault.run_initial_sync(str(source), str(backup), handler)
            self.assertEqual({p.name: p.read_text() for p in backup.iterdir()}, {n: n for n in names})


if __name__ == "__main__":
    unittest.main()
