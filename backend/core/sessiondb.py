"""
core/sessiondb.py
My Photo Diary v2 — SQLite-based session persistence (M3).

Sessions survive container restart. Cookie holds a plain token;
the DB only stores the SHA-256 hash, so DB read access does not
enable session hijacking.

Low-traffic design: one global sqlite3.Connection (WAL mode), a
threading lock serializes all operations.

Schema columns last_seen/ip/user_agent are groundwork for M4
("My devices" UI) and are populated already here.
"""

import hashlib
import logging
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from config import SESSION_DB_PATH

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    token_hash     TEXT PRIMARY KEY,
    user           TEXT NOT NULL,
    role           TEXT NOT NULL,
    personal_path  TEXT NOT NULL,
    is_demo        INTEGER NOT NULL DEFAULT 0,
    created_at     REAL NOT NULL,
    expires_at     REAL NOT NULL,
    last_seen      REAL NOT NULL,
    ip             TEXT,
    user_agent     TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);
CREATE INDEX IF NOT EXISTS idx_sessions_user    ON sessions(user);
"""

_lock = threading.Lock()
_conn: Optional[sqlite3.Connection] = None
_cleanup_counter = 0


def _ensure_conn() -> sqlite3.Connection:
    """Lazy connection init. Thread-safe via double-checked locking."""
    global _conn
    if _conn is not None:
        return _conn
    with _lock:
        if _conn is not None:
            return _conn
        path = SESSION_DB_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(
            str(path),
            check_same_thread=False,
            isolation_level=None,  # autocommit
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.executescript(_SCHEMA)
        try:
            path.chmod(0o600)
        except Exception as e:
            logger.warning("chmod 0600 on %s failed: %s", path, e)
        logger.info("Session DB loaded: %s", path)
        _conn = conn
        return _conn


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SessionRow:
    token_hash: str
    user: str
    role: str
    personal_path: Path
    is_demo: bool
    created_at: float
    expires_at: float
    last_seen: float
    ip: Optional[str]
    user_agent: Optional[str]


def _row(r: sqlite3.Row) -> SessionRow:
    return SessionRow(
        token_hash=r["token_hash"],
        user=r["user"],
        role=r["role"],
        personal_path=Path(r["personal_path"]),
        is_demo=bool(r["is_demo"]),
        created_at=r["created_at"],
        expires_at=r["expires_at"],
        last_seen=r["last_seen"],
        ip=r["ip"],
        user_agent=r["user_agent"],
    )


def create(
    *,
    user: str,
    role: str,
    personal_path: Path,
    is_demo: bool,
    ttl_seconds: int,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> str:
    """Create a new session, return the plain token for the cookie."""
    token = uuid.uuid4().hex
    now = time.time()
    conn = _ensure_conn()
    with _lock:
        conn.execute(
            """
            INSERT INTO sessions(token_hash, user, role, personal_path, is_demo,
                                 created_at, expires_at, last_seen, ip, user_agent)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _hash(token), user, role, str(personal_path), int(is_demo),
                now, now + ttl_seconds, now, ip, user_agent,
            ),
        )
    return token


def get(token: Optional[str]) -> Optional[SessionRow]:
    """
    Read session by plain token.
    - None if unknown/expired
    - On valid session: update last_seen
    - Every ~100 reads: delete expired sessions (cleanup on-path)
    """
    global _cleanup_counter
    if not token:
        return None
    conn = _ensure_conn()
    th = _hash(token)
    now = time.time()
    with _lock:
        r = conn.execute(
            "SELECT * FROM sessions WHERE token_hash = ?", (th,)
        ).fetchone()
        if r is None:
            return None
        if r["expires_at"] < now:
            conn.execute("DELETE FROM sessions WHERE token_hash = ?", (th,))
            logger.info("Expired session removed: %s", r["user"])
            return None
        conn.execute(
            "UPDATE sessions SET last_seen = ? WHERE token_hash = ?", (now, th)
        )

        _cleanup_counter += 1
        if _cleanup_counter >= 100:
            _cleanup_counter = 0
            cur = conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
            if cur.rowcount:
                logger.info("Cleanup: %d expired sessions removed", cur.rowcount)

        # Freshly read row still has old last_seen — fine for the return value.
        return _row(r)


def destroy(token: Optional[str]) -> bool:
    """Delete session. Returns True if a row was actually removed."""
    if not token:
        return False
    conn = _ensure_conn()
    with _lock:
        cur = conn.execute(
            "DELETE FROM sessions WHERE token_hash = ?", (_hash(token),)
        )
        return cur.rowcount > 0


def list_for_user(user: str) -> List[SessionRow]:
    """All active sessions for a user (preparation for the M4 UI)."""
    conn = _ensure_conn()
    now = time.time()
    with _lock:
        rows = conn.execute(
            """
            SELECT * FROM sessions
             WHERE user = ? AND expires_at > ?
             ORDER BY last_seen DESC
            """,
            (user, now),
        ).fetchall()
    return [_row(r) for r in rows]


def destroy_all_for_user(user: str) -> int:
    """Delete all sessions for a user. For admin actions like PW reset or
    user delete — every existing session becomes invalid immediately."""
    conn = _ensure_conn()
    with _lock:
        cur = conn.execute("DELETE FROM sessions WHERE user = ?", (user,))
        return cur.rowcount
