"""
core/shares.py
MPD share registry — read-only album shares via /share/<token>.

Tokens live in the same SQLite file as sessions (SESSION_DB_PATH),
separate table 'shares'. The DB only stores the SHA-256 hash of the
token; the plain token is returned to the owner only once at creation
time and never appears anywhere again.

Low-traffic design like sessiondb.py: one global sqlite3.Connection
(WAL mode), threading lock serializes all operations.
"""

import hashlib
import logging
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import List, Optional

from config import SESSION_DB_PATH

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS shares (
    token_hash    TEXT PRIMARY KEY,
    space         TEXT NOT NULL,
    album_name    TEXT NOT NULL,
    owner         TEXT NOT NULL,
    theme         TEXT,
    created_at    REAL NOT NULL,
    last_seen_at  REAL,
    enabled       INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_shares_album ON shares(space, album_name);
CREATE INDEX IF NOT EXISTS idx_shares_owner ON shares(owner);

-- Beisteuern-Links (Teilen v2, 03.09.2026): Gast-Upload in einen
-- Sammelordner — nie in Alben. Ablauf ist hier Pflicht (Ereignis-Logik).
CREATE TABLE IF NOT EXISTS contribs (
    token_hash  TEXT PRIMARY KEY,
    space       TEXT NOT NULL,
    folder_name TEXT NOT NULL,
    owner       TEXT NOT NULL,
    created_at  REAL NOT NULL,
    expires_at  REAL NOT NULL,
    max_files   INTEGER NOT NULL,
    uploaded    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_contribs_owner ON contribs(owner);
"""

_lock = threading.Lock()
_conn: Optional[sqlite3.Connection] = None


def _ensure_conn() -> sqlite3.Connection:
    """Lazy connection init (same DB file as sessions, different table)."""
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
            isolation_level=None,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.executescript(_SCHEMA)
        # Migration Teilen v2: expires_at an Bestandstabellen (03.09.2026)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(shares)")}
        if "expires_at" not in cols:
            conn.execute("ALTER TABLE shares ADD COLUMN expires_at REAL")
            logger.info("Share registry migrated: expires_at added")
        # Migration Teilen v3 (04.09.2026): Einzelfoto-Shares — file gesetzt
        # heißt "dieser Link zeigt genau ein Foto", NULL = Album-Share.
        if "file" not in cols:
            conn.execute("ALTER TABLE shares ADD COLUMN file TEXT")
            logger.info("Share registry migrated: file added (photo shares)")
        logger.info("Share registry loaded: %s", path)
        _conn = conn
        return _conn


def _hash(token: str) -> str:
    """sha256 of the share token. Only the hash is stored; a DB leak does
    not reveal usable tokens, and lookups stay constant-time on the index."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ShareInfo:
    token_hash: str
    space: str
    album_name: str
    owner: str
    theme: Optional[str]
    created_at: float
    last_seen_at: Optional[float]
    enabled: bool
    expires_at: Optional[float] = None
    # Teilen v3: gesetzt = Einzelfoto-Share (genau diese Datei), None = Album
    file: Optional[str] = None


def _row(r: sqlite3.Row) -> ShareInfo:
    """Map a sqlite3.Row to the immutable ShareInfo DTO returned by the API."""
    return ShareInfo(
        token_hash=r["token_hash"],
        space=r["space"],
        album_name=r["album_name"],
        owner=r["owner"],
        theme=r["theme"],
        created_at=r["created_at"],
        last_seen_at=r["last_seen_at"],
        enabled=bool(r["enabled"]),
        expires_at=r["expires_at"],
        file=r["file"],
    )


def create(*, space: str, album_name: str, owner: str,
           theme: Optional[str] = None,
           expires_days: Optional[int] = None,
           file: Optional[str] = None) -> str:
    """
    New share token. Returns the plain token (one-time — afterwards
    only the hash lives in the DB). expires_days=None → unbefristet
    (Bestandsverhalten); sonst läuft der Link automatisch ab.
    """
    token = secrets.token_urlsafe(32)
    now = time.time()
    expires_at = (now + expires_days * 86400) if expires_days else None
    conn = _ensure_conn()
    with _lock:
        conn.execute(
            """
            INSERT INTO shares(token_hash, space, album_name, owner, theme,
                               created_at, enabled, expires_at, file)
            VALUES(?, ?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (_hash(token), space, album_name, owner, theme, now, expires_at, file),
        )
    logger.info("Share created: %s/%s%s (owner=%s)", space, album_name,
                f"/{file}" if file else "", owner)
    return token


def resolve(token: Optional[str]) -> Optional[ShareInfo]:
    """
    Token → ShareInfo. None if unknown or disabled. Updates
    last_seen_at as a silent 'was opened' log (no counters, no
    metrics — just a timestamp for a later audit view).
    """
    if not token:
        return None
    conn = _ensure_conn()
    th = _hash(token)
    now = time.time()
    with _lock:
        r = conn.execute(
            "SELECT * FROM shares WHERE token_hash = ? AND enabled = 1", (th,)
        ).fetchone()
        if r is None:
            return None
        if r["expires_at"] and r["expires_at"] < now:
            conn.execute("DELETE FROM shares WHERE token_hash = ?", (th,))
            logger.info("Share expired and removed: %s/%s", r["space"], r["album_name"])
            return None
        conn.execute(
            "UPDATE shares SET last_seen_at = ? WHERE token_hash = ?", (now, th)
        )
        return _row(r)


def revoke(token: Optional[str]) -> bool:
    """Revoke: token row is deleted, invalid immediately."""
    if not token:
        return False
    conn = _ensure_conn()
    with _lock:
        cur = conn.execute(
            "DELETE FROM shares WHERE token_hash = ?", (_hash(token),)
        )
    return cur.rowcount > 0


def list_for_album(space: str, album_name: str) -> List[ShareInfo]:
    """All active shares for an album (for owner UI)."""
    conn = _ensure_conn()
    with _lock:
        rows = conn.execute(
            """
            SELECT * FROM shares
             WHERE space = ? AND album_name = ? AND enabled = 1
             ORDER BY created_at DESC
            """,
            (space, album_name),
        ).fetchall()
    return [_row(r) for r in rows]


def list_for_owner(owner: str) -> List[ShareInfo]:
    """All shares of an owner (for the 'which albums did I share?' view)."""
    conn = _ensure_conn()
    with _lock:
        rows = conn.execute(
            """
            SELECT * FROM shares
             WHERE owner = ? AND enabled = 1
             ORDER BY created_at DESC
            """,
            (owner,),
        ).fetchall()
    return [_row(r) for r in rows]


# ─────────────────────────────────────────────────────────────
# Beisteuern-Links (Teilen v2, 03.09.2026)
# ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ContribInfo:
    token_hash: str
    space: str
    folder_name: str
    owner: str
    created_at: float
    expires_at: float
    max_files: int
    uploaded: int


def _contrib_row(r: sqlite3.Row) -> ContribInfo:
    return ContribInfo(
        token_hash=r["token_hash"], space=r["space"],
        folder_name=r["folder_name"], owner=r["owner"],
        created_at=r["created_at"], expires_at=r["expires_at"],
        max_files=r["max_files"], uploaded=r["uploaded"],
    )


def create_contrib(*, space: str, folder_name: str, owner: str,
                   expires_days: int, max_files: int) -> str:
    token = secrets.token_urlsafe(32)
    now = time.time()
    conn = _ensure_conn()
    with _lock:
        conn.execute(
            """
            INSERT INTO contribs(token_hash, space, folder_name, owner,
                                 created_at, expires_at, max_files)
            VALUES(?, ?, ?, ?, ?, ?, ?)
            """,
            (_hash(token), space, folder_name, owner, now,
             now + expires_days * 86400, max_files),
        )
    logger.info("Contrib link created: %s/%s (owner=%s, %dd, max %d)",
                space, folder_name, owner, expires_days, max_files)
    return token


def resolve_contrib(token: Optional[str]) -> Optional[ContribInfo]:
    """Token → ContribInfo; None bei unbekannt/abgelaufen (abgelaufene
    Zeilen werden dabei entsorgt)."""
    if not token:
        return None
    conn = _ensure_conn()
    th = _hash(token)
    now = time.time()
    with _lock:
        r = conn.execute(
            "SELECT * FROM contribs WHERE token_hash = ?", (th,)
        ).fetchone()
        if r is None:
            return None
        if r["expires_at"] < now:
            conn.execute("DELETE FROM contribs WHERE token_hash = ?", (th,))
            logger.info("Contrib link expired: %s/%s", r["space"], r["folder_name"])
            return None
    return _contrib_row(r)


def count_contrib_upload(token_hash: str) -> bool:
    """Upload-Zähler atomar erhöhen — False, wenn das Limit erreicht ist."""
    conn = _ensure_conn()
    with _lock:
        cur = conn.execute(
            "UPDATE contribs SET uploaded = uploaded + 1 "
            "WHERE token_hash = ? AND uploaded < max_files",
            (token_hash,),
        )
    return cur.rowcount > 0


def list_contribs_for_owner(owner: str) -> List[ContribInfo]:
    conn = _ensure_conn()
    now = time.time()
    with _lock:
        rows = conn.execute(
            "SELECT * FROM contribs WHERE owner = ? AND expires_at >= ? "
            "ORDER BY created_at DESC",
            (owner, now),
        ).fetchall()
    return [_contrib_row(r) for r in rows]


def revoke_contrib(*, owner: str, token: Optional[str] = None,
                   token_hash: Optional[str] = None) -> bool:
    th = _hash(token) if token else token_hash
    if not th:
        return False
    conn = _ensure_conn()
    with _lock:
        cur = conn.execute(
            "DELETE FROM contribs WHERE token_hash = ? AND owner = ?",
            (th, owner),
        )
    return cur.rowcount > 0
