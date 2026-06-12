"""Daily SQLite backup — runs in a daemon thread inside each gunicorn worker.

Uses sqlite3.connect().backup() for a hot, WAL-safe copy. A timestamp file
acts as a coarse lock: only one worker (whichever checks first after the 23-hour
window) actually performs the copy; others skip silently. At worst, two workers
race within milliseconds and write the same date file twice — harmless.

Backups land in <volume_dir>/backups/ and the last 7 are kept.
"""
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone


def _backup_dir(db_path: str) -> str:
    return os.path.join(os.path.dirname(os.path.abspath(db_path)), "backups")


def run_backup_now(db_path: str) -> str | None:
    """Copy the live DB to a dated file. Returns the dest path, or None on skip/error."""
    if not os.path.isfile(db_path):
        return None

    bdir = _backup_dir(db_path)
    os.makedirs(bdir, exist_ok=True)
    stamp = os.path.join(bdir, ".last_backup")

    # Skip if a backup already ran in the last 23 hours (coarse multi-worker lock)
    if os.path.isfile(stamp) and (time.time() - os.path.getmtime(stamp)) < 23 * 3600:
        return None

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    dest = os.path.join(bdir, f"easylisting_{date_str}.sqlite")

    try:
        src = sqlite3.connect(db_path)
        dst = sqlite3.connect(dest)
        src.backup(dst)
        dst.close()
        src.close()
    except Exception:
        return None

    # Touch the stamp *after* a successful copy
    open(stamp, "w").close()  # noqa: WPS515

    # Rotate — keep the 7 most-recent dated backups
    try:
        files = sorted(
            f for f in os.listdir(bdir)
            if f.startswith("easylisting_") and f.endswith(".sqlite")
        )
        for old in files[:-7]:
            os.remove(os.path.join(bdir, old))
    except Exception:
        pass

    return dest


def start_daily_backup(db_path: str) -> None:
    """Spawn a daemon thread that checks every hour whether a backup is due."""

    def _loop() -> None:
        # Small initial delay so the app is fully up before the first check
        time.sleep(30)
        while True:
            try:
                run_backup_now(db_path)
            except Exception:
                pass
            time.sleep(3600)

    t = threading.Thread(target=_loop, daemon=True, name="db-backup")
    t.start()
