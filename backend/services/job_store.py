"""Persistent job status (SQLite) so jobs survive server restarts."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

_DB_PATH: Path | None = None


def _path() -> Path:
    global _DB_PATH
    if _DB_PATH is None:
        root = Path(__file__).resolve().parent.parent
        data = root / "data"
        data.mkdir(exist_ok=True)
        _DB_PATH = data / "jobs.sqlite"
    return _DB_PATH


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(str(_path()), check_same_thread=False)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA busy_timeout=8000")
    c.execute(
        """CREATE TABLE IF NOT EXISTS jobs (
            job_id TEXT PRIMARY KEY,
            status TEXT NOT NULL DEFAULT 'pending',
            percent INTEGER DEFAULT 0,
            error TEXT,
            payload TEXT,
            created REAL NOT NULL,
            updated REAL NOT NULL
        )"""
    )
    c.commit()
    return c


def job_put(job_id: str, blob: dict[str, Any]) -> None:
    now = time.time()
    status = str(blob.get("status", "pending"))
    percent = int(blob.get("percent", 0) or 0)
    error = blob.get("error")
    rest = {k: v for k, v in blob.items() if k not in ("status", "percent", "error")}
    payload = json.dumps(rest) if rest else None
    with _conn() as c:
        row = c.execute("SELECT created FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        created = row[0] if row else now
        c.execute(
            """INSERT OR REPLACE INTO jobs (job_id, status, percent, error, payload, created, updated)
               VALUES (?,?,?,?,?,?,?)""",
            (job_id, status, percent, error, payload, created, now),
        )
        c.commit()


def job_get(job_id: str) -> dict[str, Any]:
    with _conn() as c:
        row = c.execute(
            "SELECT status, percent, error, payload FROM jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
    if not row:
        raise KeyError(job_id)
    status, percent, error, payload = row
    out: dict[str, Any] = {"status": status, "percent": percent or 0}
    if error:
        out["error"] = error
    if payload:
        try:
            extra = json.loads(payload)
            if isinstance(extra, dict):
                out.update(extra)
        except json.JSONDecodeError:
            pass
    return out


def job_exists(job_id: str) -> bool:
    with _conn() as c:
        return c.execute("SELECT 1 FROM jobs WHERE job_id = ?", (job_id,)).fetchone() is not None


_TRANSITIVE_BROKEN = (
    "transcribing",
    "aligning",
    "correcting",
    "exporting",
    "splitting",
    "generating",
)


def recover_stale_jobs_on_startup() -> int:
    """
    After unclean shutdown, in-flight jobs would never complete. Mark them failed with a clear message.
    Leaves `pending` alone (worker may not have started).
    """
    now = time.time()
    msg = "Server restarted while this job was running. Re-run the step from the workflow."
    with _conn() as c:
        cur = c.execute(
            f"""UPDATE jobs SET status='error', error=?, updated=?
                WHERE status IN ({",".join("?" * len(_TRANSITIVE_BROKEN))})""",
            (msg, now, *_TRANSITIVE_BROKEN),
        )
        n = cur.rowcount if cur is not None else 0
        c.commit()
    return int(n)


def watchdog_reap_stale_processing(max_age_sec: float = 3600.0) -> int:
    """Re-queue safety: jobs stuck in a long-running status for > max_age_sec."""
    import time as _t

    cutoff = _t.time() - max_age_sec
    msg = "Timed out — re-run this step from the workflow."
    with _conn() as c:
        cur = c.execute(
            f"""UPDATE jobs SET status='error', error=?, updated=?
                WHERE status IN ({",".join("?" * len(_TRANSITIVE_BROKEN))})
                  AND updated < ?""",
            (msg, _t.time(), *_TRANSITIVE_BROKEN, cutoff),
        )
        n = cur.rowcount if cur is not None else 0
        c.commit()
    return int(n)
