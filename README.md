# EchoVault
**The Safe, Real-Time Backup Sentinel.**

EchoVault is a lightweight Python service that watches your folders for changes and instantly mirrors them to a backup drive. 

## Features
*   **Real-Time Sync:** Uses file system events to copy files immediately after they are saved.
*   **Safety First:** Does NOT delete files from the backup. If a file is deleted in the source, it is moved to a timestamped `_deleted_history` folder in the backup.
*   **Content Awareness:** Uses SHA-256 hashing to prevent unnecessary writes.

## Usage
1.  Install dependencies: `pip install watchdog`
2.  Edit `echo_vault.py` to add your source/destination paths.
3.  Run: `python echo_vault.py`
