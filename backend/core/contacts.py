"""
core/contacts.py
MPD address book — per-owner email contacts for Share-per-Mail.

Lives in the same SQLite file as sessions and shares (SESSION_DB_PATH),
separate table `contacts`. One entry per (owner, email). Owner only sees
their own entries — no shared family address book.

`last_used_at` enables MRU sorting: most recently used recipients first.
"""

import logging
import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import List, Optional

from config import SESSION_DB_PATH

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS contacts (
    owner          TEXT NOT NULL,
    email          TEXT NOT NULL,
    name           TEXT,
    created_at     REAL NOT NULL,
    last_used_at   REAL,
    PRIMARY KEY(owner, email)
);
CREATE INDEX IF NOT EXISTS idx_contacts_owner ON contacts(owner);
"""

_lock = threading.Lock()
_conn: Optional[sqlite3.Connection] = None


def _ensure_conn() -> sqlite3.Connection:
    global _conn
    if _conn is not None:
        return _conn
    with _lock:
        if _conn is not None:
            return _conn
        path = SESSION_DB_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.executescript(_SCHEMA)
        logger.info("Contacts registry loaded: %s", path)
        _conn = conn
        return _conn


@dataclass(frozen=True)
class Contact:
    owner: str
    email: str
    name: Optional[str]
    created_at: float
    last_used_at: Optional[float]


def _row(r: sqlite3.Row) -> Contact:
    return Contact(
        owner=r["owner"], email=r["email"], name=r["name"],
        created_at=r["created_at"], last_used_at=r["last_used_at"],
    )


def upsert(owner: str, email: str, name: Optional[str] = None) -> None:
    """
    Insert or update a contact. `last_used_at` is set to now on every
    call — so the contact stays at the top of the MRU list right after
    being used.
    """
    conn = _ensure_conn()
    now  = time.time()
    email_norm = email.strip().lower()
    name_clean = (name or "").strip() or None
    with _lock:
        conn.execute(
            """
            INSERT INTO contacts(owner, email, name, created_at, last_used_at)
            VALUES(?, ?, ?, ?, ?)
            ON CONFLICT(owner, email) DO UPDATE SET
                last_used_at = excluded.last_used_at,
                name         = COALESCE(excluded.name, contacts.name)
            """,
            (owner, email_norm, name_clean, now, now),
        )


def list_for_owner(owner: str) -> List[Contact]:
    """All contacts of an owner, MRU-sorted (most recently used first)."""
    conn = _ensure_conn()
    with _lock:
        rows = conn.execute(
            """
            SELECT * FROM contacts WHERE owner = ?
             ORDER BY COALESCE(last_used_at, created_at) DESC
            """,
            (owner,),
        ).fetchall()
    return [_row(r) for r in rows]


def delete(owner: str, email: str) -> bool:
    """Delete a contact. Returns True if deleted."""
    conn = _ensure_conn()
    with _lock:
        cur = conn.execute(
            "DELETE FROM contacts WHERE owner = ? AND email = ?",
            (owner, email.strip().lower()),
        )
    return cur.rowcount > 0
