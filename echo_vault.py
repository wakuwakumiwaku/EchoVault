import time
import os
import shutil
import hashlib
import logging
from datetime import datetime
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# --- CONFIGURATION (Default, can be overridden) ---
BACKUP_PAIRS = {
    # Add your paths here or pass them as arguments
    # r"B:": r"S:\Backup Baka",
}

IGNORE_DIRS = {
    "$RECYCLE.BIN", "System Volume Information", ".git", ".idea", "__pycache__"
}

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

def calculate_file_hash(filepath):
    """Calculates SHA256 hash of a file to verify content."""
    sha256_hash = hashlib.sha256()
    try:
        with open(filepath, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()
    except (PermissionError, OSError):
        return None

class SafeBackupHandler(FileSystemEventHandler):
    def __init__(self, source_root, dest_root):
        self.source_root = source_root
        self.dest_root = dest_root

    def _get_dest_path(self, src_path):
        rel_path = os.path.relpath(src_path, self.source_root)
        return os.path.join(self.dest_root, rel_path)

    def _is_ignored(self, src_path):
        parts = src_path.split(os.sep)
        return any(p in IGNORE_DIRS for p in parts)

    def on_modified(self, event):
        if event.is_directory or self._is_ignored(event.src_path):
            return
        time.sleep(1.0)
        self.sync_file(event.src_path)

    def on_created(self, event):
        if event.is_directory or self._is_ignored(event.src_path):
            return
        time.sleep(1.0)
        self.sync_file(event.src_path)

    def on_moved(self, event):
        if self._is_ignored(event.src_path):
            return
        if not event.is_directory:
            # Rename = Delete Old (Archive) + Create New (Sync)
            # Watchdog provides src_path (old) and dest_path (new)
            
            # 1. Sync the NEW file
            self.sync_file(event.dest_path)
            
            # 2. Archive the OLD file from backup (if it exists)
            # We must calculate where the old file WAS in the backup
            old_backup_path = self._get_dest_path(event.src_path)
            if os.path.exists(old_backup_path):
                self._archive_specific_file(old_backup_path)

    def on_deleted(self, event):
        if event.is_directory or self._is_ignored(event.src_path):
            return
        logging.warning(f"File Deleted in Source: {event.src_path}")
        self.archive_file(event.src_path)

    def sync_file(self, src_path):
        dest_path = self._get_dest_path(src_path)
        try:
            if not os.path.exists(src_path):
                return

            if os.path.exists(dest_path):
                src_hash = calculate_file_hash(src_path)
                dest_hash = calculate_file_hash(dest_path)
                if src_hash and dest_hash and src_hash == dest_hash:
                    return

            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
            shutil.copy2(src_path, dest_path)
            logging.info(f"SYNCED: {src_path} -> {dest_path}")
        except Exception as e:
            logging.error(f"Failed to sync {src_path}: {e}")

    def archive_file(self, src_path):
        """Moves the BACKUP copy to a _deleted_history folder."""
        dest_path = self._get_dest_path(src_path)
        self._archive_specific_file(dest_path)

    def _archive_specific_file(self, backup_file_path):
        """Internal helper to move a specific file to history."""
        if os.path.exists(backup_file_path):
            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            filename = os.path.basename(backup_file_path)
            history_dir = os.path.join(self.dest_root, "_deleted_history", timestamp)
            os.makedirs(history_dir, exist_ok=True)
            target_archive = os.path.join(history_dir, filename)
            try:
                shutil.move(backup_file_path, target_archive)
                logging.info(f"ARCHIVED: {backup_file_path} -> {target_archive}")
            except Exception as e:
                logging.error(f"Failed to archive {backup_file_path}: {e}")

if __name__ == "__main__":
    observer = Observer()
    print("--- EchoVault Backup Sentinel ---")
    
    # You can add your paths here
    paths = {
        r"B:": r"S:\Backup Baka",
        r"G:": r"S:\Backup Sherpa",
        r"J:\MCfragen": r"S:\Backup Twentytons\MC Fragen"
    }

    for src, dest in paths.items():
        if os.path.exists(src):
            print(f"Watching: {src} -> {dest}")
            event_handler = SafeBackupHandler(src, dest)
            observer.schedule(event_handler, src, recursive=True)
        else:
            logging.error(f"Source path not found: {src}")

    observer.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
