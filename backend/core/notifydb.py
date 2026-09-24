"""
core/notifydb.py
My Photo Diary v2 — Benachrichtigungs-Store + Geräteregister (additiv 09/2026).

Ersatz für ntfy aus Sicht der nativen App (de.bgghome.mpd): Ereignisse
landen hier statt (nur) beim ntfy-Push, Geräte holen sie per Poll/Long-Poll
ab. Gleiches Low-Traffic-Muster wie core/sessiondb.py: eine globale
WAL-Connection, ein threading.Lock serialisiert alles.

Bewusste Abgrenzung (01.09.2026): Hier liegen ausschließlich
**Wegwerf-Betriebsdaten** — rekonstruierbar, nach Wochen wertlos, kein
Lock-in. Die Tagebuchdaten (album.json/PDX) bleiben unberührt JSON.
Als Sichtfenster schreibt jeder strukturelle Schreibvorgang zusätzlich
einen menschenlesbaren SQL-Dump neben die DB (atomar via tmp+rename) —
lesbar auch ohne MPD, und jede Sicherung des Datenordners nimmt ihn mit.

- notifications: fortlaufende id = Poll-Cursor. recipient ist ein
  Username oder '*' (alle). payload ist ein frei belegbares JSON-Objekt
  (Deep-Link, Thumb-URL, …). Einträge älter als RETENTION_DAYS werden
  beim Einfügen aufgeräumt.
- devices: App-generierte device_id (UUID), gebunden an den User der
  Session, mit eigenem Cursor last_acked_id.
"""

import json
import logging
import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import List, Optional

from config import NOTIFY_DB_PATH

logger = logging.getLogger(__name__)

RETENTION_DAYS = 30

_SCHEMA = """
CREATE TABLE IF NOT EXISTS notifications (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at REAL NOT NULL,
    recipient  TEXT NOT NULL,          -- Username oder '*'
    type       TEXT NOT NULL,          -- z. B. 'memories'
    title      TEXT NOT NULL,
    body       TEXT NOT NULL,
    payload    TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_notif_recipient ON notifications(recipient, id);

-- Journal fürs Upload-Tages-Digest (19:00-Job): jede Einsortierung wird
-- vermerkt und beim Digest-Lauf zu Sammelmeldungen verdichtet + geleert.
CREATE TABLE IF NOT EXISTS digest_journal (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts       REAL NOT NULL,
    user     TEXT NOT NULL,          -- Verursacher (bekommt die Meldung NICHT)
    space    TEXT NOT NULL,
    album    TEXT NOT NULL,
    filename TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS devices (
    device_id     TEXT PRIMARY KEY,    -- App-generierte UUID
    user          TEXT NOT NULL,
    name          TEXT,
    platform      TEXT,
    transport     TEXT NOT NULL DEFAULT 'poll',
    created_at    REAL NOT NULL,
    last_seen     REAL NOT NULL,
    last_acked_id INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_devices_user ON devices(user);
"""

_lock = threading.Lock()
_conn: Optional[sqlite3.Connection] = None

# Sofort-Weckung wartender Long-Polls (routers/notify.py registriert hier
# einen threadsicheren Weckruf). Fehler im Hook dürfen nie einen
# Schreibvorgang verhindern.
on_notify = None  # type: ignore[assignment]


def _fire_on_notify() -> None:
    if on_notify is not None:
        try:
            on_notify()
        except Exception as e:
            logger.debug("on_notify-Hook fehlgeschlagen: %s", e)


def _ensure_conn() -> sqlite3.Connection:
    """Lazy connection init. Thread-safe via double-checked locking."""
    global _conn
    if _conn is not None:
        return _conn
    with _lock:
        if _conn is not None:
            return _conn
        path = NOTIFY_DB_PATH
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
        logger.info("Notify DB loaded: %s", path)
        _conn = conn
        return _conn


def _write_dump(conn: sqlite3.Connection) -> None:
    """Menschenlesbarer SQL-Dump neben der DB, atomar (tmp + rename).

    Wird nur bei strukturellen Änderungen gerufen (Ereignis, Ack,
    Registrierung), nicht bei last_seen-Updates — die Datei ist damit
    praktisch immer aktuell, ohne bei jedem Poll zu schreiben.
    Fehler beim Dump dürfen nie eine API-Antwort verhindern.
    """
    try:
        dump_path = NOTIFY_DB_PATH.with_suffix(".sql")
        tmp = dump_path.with_suffix(".sql.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("-- My Photo Diary — Benachrichtigungs-Store\n")
            f.write(f"-- Stand: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write("-- Betriebsdaten (rekonstruierbar) — die Tagebuchdaten"
                    " liegen in den album.json.\n\n")
            for line in conn.iterdump():
                f.write(line + "\n")
        os.replace(tmp, dump_path)
    except Exception as e:
        logger.warning("Notify-Dump fehlgeschlagen: %s", e)


@dataclass(frozen=True)
class Notification:
    id: int
    created_at: float
    recipient: str
    type: str
    title: str
    body: str
    payload: dict


@dataclass(frozen=True)
class DeviceRow:
    device_id: str
    user: str
    name: Optional[str]
    platform: Optional[str]
    transport: str
    created_at: float
    last_seen: float
    last_acked_id: int


def _notif(r: sqlite3.Row) -> Notification:
    try:
        payload = json.loads(r["payload"])
    except Exception:
        payload = {}
    return Notification(
        id=r["id"], created_at=r["created_at"], recipient=r["recipient"],
        type=r["type"], title=r["title"], body=r["body"], payload=payload,
    )


def _device(r: sqlite3.Row) -> DeviceRow:
    return DeviceRow(
        device_id=r["device_id"], user=r["user"], name=r["name"],
        platform=r["platform"], transport=r["transport"],
        created_at=r["created_at"], last_seen=r["last_seen"],
        last_acked_id=r["last_acked_id"],
    )


# ---------------------------------------------------------------------------
# Ereignisquelle
# ---------------------------------------------------------------------------

def notify(*, recipient: str, type: str, title: str, body: str,
           payload: Optional[dict] = None) -> int:
    """Ereignis eintragen. recipient: Username oder '*'. Gibt die id zurück."""
    now = time.time()
    conn = _ensure_conn()
    with _lock:
        cur = conn.execute(
            """
            INSERT INTO notifications(created_at, recipient, type, title, body, payload)
            VALUES(?, ?, ?, ?, ?, ?)
            """,
            (now, recipient, type, title, body,
             json.dumps(payload or {}, ensure_ascii=False)),
        )
        # Retention beim Einfügen — kein eigener Aufräum-Job nötig
        conn.execute(
            "DELETE FROM notifications WHERE created_at < ?",
            (now - RETENTION_DAYS * 86400,),
        )
        _write_dump(conn)
    _fire_on_notify()
    return cur.lastrowid


def notify_many(*, recipients: List[str], type: str, title: str, body: str,
                payload: Optional[dict] = None) -> List[int]:
    """Gezielte Zustellung (09/2026): eine Zeile je Empfänger — so gehen
    „nur Admins" (alert) und „alle außer Verursacher" (upload), ohne dass
    sich an Pull/Ack/Schema etwas ändert. Ein Lock, ein Dump."""
    if not recipients:
        return []
    now = time.time()
    payload_json = json.dumps(payload or {}, ensure_ascii=False)
    ids: List[int] = []
    conn = _ensure_conn()
    with _lock:
        for r in recipients:
            cur = conn.execute(
                """
                INSERT INTO notifications(created_at, recipient, type, title, body, payload)
                VALUES(?, ?, ?, ?, ?, ?)
                """,
                (now, r, type, title, body, payload_json),
            )
            ids.append(cur.lastrowid)
        conn.execute(
            "DELETE FROM notifications WHERE created_at < ?",
            (now - RETENTION_DAYS * 86400,),
        )
        _write_dump(conn)
    _fire_on_notify()
    return ids


# ---------------------------------------------------------------------------
# Digest-Journal (Upload-Tages-Digest)
# ---------------------------------------------------------------------------

def journal_import(*, user: str, space: str, album: str, filename: str) -> None:
    """Einsortierung fürs 19:00-Digest vermerken — bewusst ohne Dump
    (Betriebsrauschen; die Sammelmeldung selbst landet ohnehin im Dump)."""
    conn = _ensure_conn()
    with _lock:
        conn.execute(
            "INSERT INTO digest_journal(ts, user, space, album, filename) VALUES(?, ?, ?, ?, ?)",
            (time.time(), user, space, album, filename),
        )


def drain_journal() -> List[dict]:
    """Alle Journal-Einträge holen und löschen (Digest-Lauf)."""
    conn = _ensure_conn()
    with _lock:
        rows = conn.execute(
            "SELECT ts, user, space, album, filename FROM digest_journal ORDER BY id"
        ).fetchall()
        conn.execute("DELETE FROM digest_journal")
    return [dict(r) for r in rows]


def pull(*, user: str, since: int, limit: int = 100) -> List[Notification]:
    """Ereignisse für den User (eigene + Broadcast '*') mit id > since."""
    conn = _ensure_conn()
    with _lock:
        rows = conn.execute(
            """
            SELECT * FROM notifications
             WHERE id > ? AND recipient IN ('*', ?)
             ORDER BY id
             LIMIT ?
            """,
            (since, user, limit),
        ).fetchall()
    return [_notif(r) for r in rows]


# ---------------------------------------------------------------------------
# Geräteregister
# ---------------------------------------------------------------------------

def register_device(*, device_id: str, user: str, name: Optional[str],
                    platform: Optional[str], transport: str = "poll") -> Optional[DeviceRow]:
    """Gerät anlegen oder aktualisieren (Upsert). None, wenn die device_id
    bereits einem anderen User gehört."""
    now = time.time()
    conn = _ensure_conn()
    with _lock:
        r = conn.execute(
            "SELECT * FROM devices WHERE device_id = ?", (device_id,)
        ).fetchone()
        if r is not None and r["user"] != user:
            return None
        if r is None:
            conn.execute(
                """
                INSERT INTO devices(device_id, user, name, platform, transport,
                                    created_at, last_seen, last_acked_id)
                VALUES(?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (device_id, user, name, platform, transport, now, now),
            )
        else:
            conn.execute(
                """
                UPDATE devices SET name = ?, platform = ?, transport = ?, last_seen = ?
                 WHERE device_id = ?
                """,
                (name if name is not None else r["name"],
                 platform if platform is not None else r["platform"],
                 transport, now, device_id),
            )
        _write_dump(conn)
        row = conn.execute(
            "SELECT * FROM devices WHERE device_id = ?", (device_id,)
        ).fetchone()
    return _device(row)


def get_device(device_id: str) -> Optional[DeviceRow]:
    conn = _ensure_conn()
    with _lock:
        r = conn.execute(
            "SELECT * FROM devices WHERE device_id = ?", (device_id,)
        ).fetchone()
    return _device(r) if r else None


def touch_device(device_id: str) -> None:
    """last_seen aktualisieren (beim Poll) — bewusst ohne Dump."""
    conn = _ensure_conn()
    with _lock:
        conn.execute(
            "UPDATE devices SET last_seen = ? WHERE device_id = ?",
            (time.time(), device_id),
        )


def ack_device(*, device_id: str, user: str, last_id: int) -> bool:
    """Cursor setzen. False, wenn Gerät unbekannt oder fremder User."""
    conn = _ensure_conn()
    with _lock:
        cur = conn.execute(
            """
            UPDATE devices SET last_acked_id = ?, last_seen = ?
             WHERE device_id = ? AND user = ?
            """,
            (last_id, time.time(), device_id, user),
        )
        if cur.rowcount == 0:
            return False
        _write_dump(conn)
        return True


def list_devices(user: str) -> List[DeviceRow]:
    conn = _ensure_conn()
    with _lock:
        rows = conn.execute(
            "SELECT * FROM devices WHERE user = ? ORDER BY last_seen DESC",
            (user,),
        ).fetchall()
    return [_device(r) for r in rows]


def delete_device(*, device_id: str, user: str) -> bool:
    conn = _ensure_conn()
    with _lock:
        cur = conn.execute(
            "DELETE FROM devices WHERE device_id = ? AND user = ?",
            (device_id, user),
        )
        if cur.rowcount == 0:
            return False
        _write_dump(conn)
        return True
