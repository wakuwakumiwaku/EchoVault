import unittest
import os
import shutil
import time
import tempfile
from watchdog.observers import Observer
from echo_vault import SafeBackupHandler

class TestEchoVault(unittest.TestCase):
    def setUp(self):
        # Create temporary directories for testing
        self.test_dir = tempfile.mkdtemp()
        self.src_dir = os.path.join(self.test_dir, "source")
        self.dest_dir = os.path.join(self.test_dir, "backup")
        os.makedirs(self.src_dir)
        os.makedirs(self.dest_dir)

        # Setup Observer
        self.observer = Observer()
        self.handler = SafeBackupHandler(self.src_dir, self.dest_dir)
        self.observer.schedule(self.handler, self.src_dir, recursive=True)
        self.observer.start()
        
        # Give watchdog a moment to start
        time.sleep(1)

    def tearDown(self):
        self.observer.stop()
        self.observer.join()
        try:
            shutil.rmtree(self.test_dir)
        except PermissionError:
            pass # Sometimes windows holds onto file handles

    def test_file_creation_sync(self):
        print("\n[TEST] Creating file...")
        src_file = os.path.join(self.src_dir, "test.txt")
        dest_file = os.path.join(self.dest_dir, "test.txt")

        with open(src_file, "w") as f:
            f.write("Hello World")
        
        # Allow sync time (handler has 1s sleep + debounce)
        time.sleep(2.0)

        self.assertTrue(os.path.exists(dest_file), "File should be synced to backup")
        with open(dest_file, "r") as f:
            content = f.read()
        self.assertEqual(content, "Hello World")

    def test_file_modification_sync(self):
        print("\n[TEST] Modifying file...")
        src_file = os.path.join(self.src_dir, "modify.txt")
        dest_file = os.path.join(self.dest_dir, "modify.txt")

        # Initial creation
        with open(src_file, "w") as f:
            f.write("Version 1")
        time.sleep(2.0)

        # Modification
        with open(src_file, "w") as f:
            f.write("Version 2 Updated")
        time.sleep(2.0)

        with open(dest_file, "r") as f:
            content = f.read()
        self.assertEqual(content, "Version 2 Updated")

    def test_file_deletion_archive(self):
        print("\n[TEST] Deleting file (Archiving)...")
        src_file = os.path.join(self.src_dir, "delete_me.txt")
        dest_file = os.path.join(self.dest_dir, "delete_me.txt")

        # Create and sync
        with open(src_file, "w") as f:
            f.write("Important Data")
        time.sleep(2.0)
        
        # Delete source
        os.remove(src_file)
        time.sleep(2.0)

        # Verify it's gone from main backup folder
        self.assertFalse(os.path.exists(dest_file), "File should be moved from main backup")

        # Verify it exists in _deleted_history
        history_dir = os.path.join(self.dest_dir, "_deleted_history")
        self.assertTrue(os.path.exists(history_dir), "History folder should be created")
        
        # Check inside the timestamp folder
        timestamp_folders = os.listdir(history_dir)
        if not timestamp_folders:
            self.fail("No timestamp folder found in history")
            
        archived_file = os.path.join(history_dir, timestamp_folders[0], "delete_me.txt")
        self.assertTrue(os.path.exists(archived_file), "File should be inside history folder")

if __name__ == "__main__":
    unittest.main()
