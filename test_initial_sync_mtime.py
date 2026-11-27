import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from echo_vault import SafeBackupHandler, run_initial_sync


class InitialSyncMtimeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.source = Path(temp.name) / "source" / "notes.txt"
        self.backup = Path(temp.name) / "backup" / "notes.txt"
        self.source.parent.mkdir()
        self.backup.parent.mkdir()
        self.handler = SafeBackupHandler(str(self.source.parent), str(self.backup.parent))
        self.history = self.backup.parent / "_deleted_history"

    def scan(self):
        run_initial_sync(str(self.source.parent), str(self.backup.parent), self.handler)

    def test_subsecond_edit_replaces_backup_and_archives_previous_bytes(self):
        before = 1_700_000_000_000_000_000
        after = before + 100  # 100 ns; even float timestamps can compare equal here.
        self.backup.write_bytes(b"old")
        self.source.write_bytes(b"new")
        os.utime(self.backup, ns=(before, before))
        os.utime(self.source, ns=(after, after))
        if self.source.stat().st_mtime_ns == self.backup.stat().st_mtime_ns:
            self.skipTest("filesystem cannot distinguish the test timestamps")

        self.scan()

        self.assertEqual(self.backup.read_bytes(), b"new")
        self.assertEqual(self.source.read_bytes(), b"new")
        self.assertEqual(self.source.stat().st_mtime_ns, after)
        archived = [(p.name, p.read_bytes()) for p in self.history.glob("*/*")]
        self.assertEqual(archived, [("notes.txt", b"old")])

    def test_equal_size_and_mtime_skip_sync(self):
        stamp = 1_700_000_000_000_000_000
        for path in (self.source, self.backup):
            path.write_bytes(b"same")
            os.utime(path, ns=(stamp, stamp))
        with patch.object(self.handler, "sync_file", wraps=self.handler.sync_file) as sync:
            self.scan()
        sync.assert_not_called()
        self.assertFalse(self.history.exists())