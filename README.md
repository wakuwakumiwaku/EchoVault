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

### `Smart Integrity Checks`
Uses SHA-256 hashing to verify file content, ensuring only actual changes trigger a write operation.

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
