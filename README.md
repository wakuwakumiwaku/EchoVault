# EchoVault
> **The Silent Sentinel for Your Data.**

![Python Version](https://img.shields.io/badge/python-3.8+-blue.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)
![Status](https://img.shields.io/badge/status-stable-brightgreen.svg)

**EchoVault** is a robust, lightweight, and real-time backup utility written in Python. It acts as a silent guardian for your most critical directories, instantly mirroring changes to a secure backup location while preserving file history.

> [!IMPORTANT]
> **Safety First Protocol**
> Unlike standard sync tools that propagate deletions immediately, **EchoVault** prioritizes data integrity. When a file is modified or deleted in the source, the old version is **archived** rather than destroyed, giving you a fail-safe against accidental data loss.

---

## Key Features

### `Real-Time Synchronization`
Leverages `watchdog` to detect file system events (create, move, modify) the moment they happen.

### `Safe Deletion Protocol`
Files deleted in the source are **not** deleted in the destination. Instead, they are moved to a timestamped `_deleted_history` archive.

If history-directory creation or archive-name setup fails with a filesystem error, archiving logs the failure and leaves the active backup and existing history untouched. Delete/move event dispatch can continue processing later events; the failed archive is not automatically retried. A rename syncs the new path before attempting to archive the old backup. If the new-path sync fails or the renamed source is missing, EchoVault logs that archival is deferred and retains the old active backup at its original path. Replaying the move event after recovery can complete the sync and archival; no automatic retry is scheduled. An already-identical new backup counts as a successful sync. If only the subsequent old-backup archival fails, both active paths remain available.

File moves are classified using both endpoints and `IGNORE_DIRS`. Moving a file out of an ignored directory (for example, `.git/notes.txt` to `notes.txt`) syncs the newly included path without touching any backup under the ignored old path. Moving into an ignored directory archives the old included backup as a deletion and never copies the excluded destination. Moves wholly inside ignored directories are ignored; moves between included paths retain the sync-before-archive behavior above. Failed syncs or archives can be retried by replaying the event after recovery; no automatic retry is scheduled. Directory move events themselves remain ignored.

### `Smart Integrity Checks`
Uses SHA-256 hashing to verify file content, ensuring only actual changes trigger a write operation.

Copies are staged in the destination directory before atomic replacement. Before replacing an existing backup, EchoVault copies that backup's previous contents and metadata into `_deleted_history/YYYY-MM-DD_HH-MM-SS/`. Colliding names receive numeric suffixes, preserving repeated versions and same-named files from different directories. Unchanged content and first-time backups do not create history.

A failed source copy, history snapshot, or replacement leaves the previous active backup at its original path instead of publishing a partial file; a failed history snapshot aborts the sync without logging `SYNCED`. Handled failures clean up the staging file and attempt to remove incomplete history copies. If replacement fails after a successful snapshot, that historical copy remains; retrying may create another copy of the same old version. History creation and active-file replacement are separate operations, not one crash-atomic transaction.

### `Fast Initial Sync`
On startup, performs a rapid state comparison (using size & modification time) to bring the backup up to date before watching for real-time events.

### `Silent Operation`
Includes a VBScript launcher (`silent_launcher.vbs`) to run the process invisibly in the background.

---

## Getting Started

### Prerequisites
- Python 3.8 or higher
- `pip` (Python Package Manager)

### Installation

1.  **Clone the Repository**
    ```bash
    git clone https://github.com/wakuwakumiwaku/echovault.git
    cd echovault
    ```

2.  **Install Dependencies**
    ```bash
    pip install -r requirements.txt
    ```

---

## Configuration

Open `echo_vault.py` in your favorite text editor. Locate the `BACKUP_PAIRS` (or `paths` in the main block) configuration section:

```python
# Configure your source and destination paths here
paths = {
    r"C:\Users\You\Documents\Important": r"D:\Backups\Important",
    r"C:\Users\You\Pictures": r"E:\NetworkDrive\Pictures_Backup"
}
```

-   **Key**: The source directory you want to watch.
-   **Value**: The destination directory where backups will be stored.

Source and destination roots must be disjoint: neither may equal or contain the other. EchoVault rejects overlapping roots with `ValueError` before initial sync, preventing recursive backups and relative-path mappings that could overwrite source files. Validation resolves existing directory symlinks/junctions and normalizes path components and platform casing; sibling directories and different Windows drives remain valid. This is a startup configuration check, not protection against later filesystem alias changes. Each pair is checked independently; also keep every pair's destination outside all other watched source roots.

> [!TIP]
> Use raw strings (`r"..."`) for Windows paths to avoid escape character issues.

---

## Usage

### Manual Start
Run the script directly in your terminal to see real-time logs:

```bash
python echo_vault.py
```
*Press `Ctrl+C` to stop.*

### Background Mode (Windows)
To run EchoVault silently in the background (perfect for startup):

1.  Locate `silent_launcher.vbs`.
2.  Double-click it.
    *   *Nothing will appear on screen, but the python process will start running.*
3.  To stop it, use Task Manager to end the `python.exe` process or use a task killer script.

---

## Project Structure

```text
EchoVault/
├── echo_vault.py        # Core logic: Watchdog observer & sync handler
├── silent_launcher.vbs  # Windows script to launch without a console window
├── requirements.txt     # Python dependencies
├── .gitignore           # Git ignore rules
└── README.md            # Documentation
```

---

## Tests

```bash
python -m unittest discover -v
```

Tests use temporary synthetic source/backup directories and explicit watchdog events, not a live observer or the configured backup paths. The modified-file regressions exercise event dispatch, hashing, staging, history preservation, and replacement together, including injected filesystem failures.

---

## Contributing

Contributions are welcome!
1.  Fork the Project
2.  Create your Feature Branch (`git checkout -b feature/AmazingFeature`)
3.  Commit your Changes (`git commit -m 'Add some AmazingFeature'`)
4.  Push to the Branch (`git push origin feature/AmazingFeature`)
5.  Open a Pull Request

---

## License

Distributed under the MIT License. See `LICENSE` for more information.
