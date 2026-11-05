"""Backup writes and archives stay inside the configured destination root."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from watchdog.events import FileCreatedEvent, FileDeletedEvent, FileMovedEvent
from echo_vault import SafeBackupHandler, run_initial_sync


class PathContainmentTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='echovault-containment-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / 'source'
        self.backup = self.root / 'backup'
        self.protected = self.root / 'protected'
        for directory in (self.source, self.backup, self.protected):
            directory.mkdir()
        self.canary = self.protected / 'notes.txt'
        self.canary.write_bytes(b'protected bytes')
        self.handler = SafeBackupHandler(str(self.source), str(self.backup))
        sleep = patch('echo_vault.time.sleep')
        sleep.start()
        self.addCleanup(sleep.stop)
        watcher = patch('watchdog.observers.api.BaseObserver.start',
                        side_effect=AssertionError('live watchers are forbidden'))
        watcher.start()
        self.addCleanup(watcher.stop)

    def link(self, path, target):
        try:
            path.symlink_to(target, target_is_directory=target.is_dir())
        except (NotImplementedError, OSError) as error:
            self.skipTest(str(error))

    def assert_protected(self):
        self.assertTrue(self.canary.is_file(), 'archive must not move an external file')
        self.assertEqual(self.canary.read_bytes(), b'protected bytes')
        self.assertEqual({p.name for p in self.protected.iterdir()}, {'notes.txt'})
        self.assertEqual(list(self.backup.rglob('.echovault-*.tmp')), [])

    def normal_event(self):
        normal = self.source / 'normal.txt'
        normal.write_bytes(b'later event')
        self.handler.dispatch(FileCreatedEvent(str(normal)))
        self.assertEqual((self.backup / normal.name).read_bytes(), b'later event')

    def test_destination_directory_link_cannot_overwrite_external_file(self):
        folder = self.source / 'linked'
        folder.mkdir()
        source = folder / 'notes.txt'
        source.write_bytes(b'new source')
        self.link(self.backup / 'linked', self.protected)
        self.assertFalse(self.handler.sync_file(str(source)), 'escaping destination must be refused')
        self.assert_protected()
        self.normal_event()

    def test_deleted_event_cannot_archive_through_escaping_directory_link(self):
        self.link(self.backup / 'linked', self.protected)
        self.handler.dispatch(FileDeletedEvent(str(self.source / 'linked' / 'notes.txt')))
        self.assert_protected()
        self.normal_event()

    def test_outside_source_event_cannot_map_to_sibling_destination(self):
        outside = self.root / 'outside'
        outside.mkdir()
        source = outside / 'notes.txt'
        source.write_bytes(b'outside source')
        self.handler.dispatch(FileDeletedEvent(str(source)))
        self.assertTrue(source.is_file(), 'an outside-source event must not archive the external file')
        self.assertEqual(source.read_bytes(), b'outside source')
        self.assertEqual({p.name for p in outside.iterdir()}, {'notes.txt'})
        self.assertEqual(list(self.backup.iterdir()), [], 'out-of-root events must not create backup history')
        self.normal_event()

    def test_history_directory_link_aborts_replacement_without_writing_outside(self):
        source = self.source / 'notes.txt'
        source.write_bytes(b'new source')
        active = self.backup / 'notes.txt'
        active.write_bytes(b'old active')
        self.link(self.backup / '_deleted_history', self.protected)
        self.assertFalse(self.handler.sync_file(str(source)), 'uncontained history must abort the sync')
        self.assertEqual(active.read_bytes(), b'old active')
        self.assert_protected()

    def test_escaping_leaf_symlink_is_not_archived_or_replaced(self):
        source = self.source / 'notes.txt'
        source.write_bytes(b'new source')
        active = self.backup / 'notes.txt'
        self.link(active, self.canary)
        self.handler.dispatch(FileCreatedEvent(str(source)))
        self.assertTrue(active.is_symlink(), 'escaping leaf link must be left alone')
        self.handler.dispatch(FileDeletedEvent(str(source)))
        self.assertTrue(active.is_symlink())
        self.assert_protected()

    def test_initial_sync_skips_escaping_path_and_continues_with_normal_files(self):
        folder = self.source / 'linked'
        folder.mkdir()
        (folder / 'notes.txt').write_bytes(b'new source')
        (self.source / 'ordinary.txt').write_bytes(b'ordinary')
        self.link(self.backup / 'linked', self.protected)
        run_initial_sync(str(self.source), str(self.backup), self.handler)
        self.assert_protected()
        self.assertEqual((self.backup / 'ordinary.txt').read_bytes(), b'ordinary')

    def test_failed_escaping_move_preserves_old_active_backup(self):
        self.link(self.backup / 'linked', self.protected)
        folder = self.source / 'linked'
        folder.mkdir()
        (folder / 'notes.txt').write_bytes(b'new source')
        old = self.backup / 'old.txt'
        old.write_bytes(b'old backup')
        self.handler.dispatch(FileMovedEvent(str(self.source / 'old.txt'), str(folder / 'notes.txt')))
        self.assertTrue(old.is_file(), 'failed moved-to path must preserve the active old backup')
        self.assertEqual(old.read_bytes(), b'old backup')
        self.assert_protected()

    def test_in_root_directory_alias_remains_usable(self):
        real = self.backup / 'real'
        real.mkdir()
        self.link(self.backup / 'alias', real)
        folder = self.source / 'alias'
        folder.mkdir()
        source = folder / 'notes.txt'
        source.write_bytes(b'new source')
        self.assertTrue(self.handler.sync_file(str(source)))
        self.assertEqual((real / source.name).read_bytes(), b'new source')
        self.assert_protected()


if __name__ == '__main__':
    unittest.main()
