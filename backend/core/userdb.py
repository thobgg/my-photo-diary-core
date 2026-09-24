"""
core/userdb.py
My Photo Diary v2 — own user file (DSM-decoupled, M1)

Reads users.json, verifies passwords via Argon2id.
Writes happen exclusively through scripts/manage_users.py.

Schema:
{
  "version": 1,
  "users": {
    "anna": {
      "pw_hash": "$argon2id$...",
      "role": "admin|editor|viewer",
      "personal_path": "/volume1/homes/anna/Photos",
      "is_demo": false,
      "modules": ["tours", "stats"]   // optional; fehlt = alle Module frei (Grandfather)
    },
    ...
  }
}

Hashes are never logged. Password only in memory during verify.
"""

import json
import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, FrozenSet, Optional

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHashError

from config import USERS_JSON_PATH, default_personal_path

logger = logging.getLogger(__name__)

# OWASP 2024 defaults: 64 MiB, 3 iterations, 4 parallel lanes
_ph = PasswordHasher(
    time_cost=3,
    memory_cost=64 * 1024,
    parallelism=4,
)

# Dummy hash against timing attacks (user enumeration).
# Generated once at import → guaranteed valid format → verify() does
# the full CPU work even when the user doesn't exist.
_DUMMY_HASH = _ph.hash("not-a-real-password")

ALLOWED_ROLES = {"admin", "editor", "viewer"}
SCHEMA_VERSION = 1

_lock = threading.Lock()
_cache: Optional[Dict] = None
_cache_mtime: float = 0.0


@dataclass(frozen=True)
class UserRecord:
    username: str
    role: str
    personal_path: Path
    is_demo: bool
    display_name: Optional[str] = None  # display name (optional, e.g. "Anna")
    # Freigeschaltete Zusatzmodule. None = Feld fehlt in users.json = alle frei
    # (Grandfather). Leere Menge = nur Grundsoftware.
    modules: Optional[FrozenSet[str]] = None
    # Memories-Quelle je Nutzer (03.09.2026): shared (Default) | personal
    memories_scope: str = "shared"
    # Erstanmelde-Zwangswechsel (04.09.2026): true = nur noch
    # Passwort-Änderung erlaubt, bis ein neues Passwort gesetzt ist.
    must_change_password: bool = False
    # Teilen nach aussen (06.09.2026). Vorgabe: erlaubt — das Feld ist ein
    # Ausnahmeschalter fuer einzelne Konten (Kinderkonto: eigenes Tagebuch
    # ja, veroeffentlichen nein), kein neues Standardverhalten. Fehlt es,
    # bleibt alles wie bisher.
    may_share: bool = True


def _parse_scope(entry: dict) -> str:
    scope = entry.get("memories_scope")
    return scope if scope in ("shared", "personal") else "shared"


def _parse_modules(entry: dict) -> Optional[FrozenSet[str]]:
    mods = entry.get("modules")
    return frozenset(mods) if isinstance(mods, list) else None


def _load() -> Dict[str, dict]:
    """Read users.json on demand. Thread-safe. Returns {} on error."""
    global _cache, _cache_mtime
    path = USERS_JSON_PATH
    try:
        mtime = path.stat().st_mtime
    except FileNotFoundError:
        logger.warning("users.json not found: %s", path)
        with _lock:
            _cache = {}
            _cache_mtime = 0.0
        return {}
    except Exception as e:
        logger.error("users.json stat error: %s", e)
        return _cache or {}

    with _lock:
        if _cache is not None and mtime == _cache_mtime:
            return _cache
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            logger.error("users.json parse error: %s", e)
            return _cache or {}

        if data.get("version") != SCHEMA_VERSION:
            logger.error("users.json: unknown schema version %r", data.get("version"))
            return _cache or {}

        users = data.get("users") or {}
        if not isinstance(users, dict):
            logger.error("users.json: 'users' is not an object")
            return _cache or {}

        _cache = users
        _cache_mtime = mtime
        logger.info("users.json loaded: %d users", len(users))
        return users


def authenticate(username: str, password: str) -> Optional[UserRecord]:
    """
    Verifies username/password against users.json.
    Returns UserRecord on success, otherwise None.
    Password is NEVER logged.
    """
    users = _load()
    entry = users.get(username)
    if entry is None:
        # Dummy verify: guarantees same CPU cost as a real verify
        try:
            _ph.verify(_DUMMY_HASH, password)
        except Exception:
            pass
        return None

    pw_hash = entry.get("pw_hash") or ""
    if not pw_hash:
        logger.warning("User %s has no pw_hash set", username)
        return None

    try:
        _ph.verify(pw_hash, password)
    except VerifyMismatchError:
        return None
    except InvalidHashError as e:
        logger.error("User %s: invalid hash format: %s", username, e)
        return None
    except Exception as e:
        logger.error("Argon2 verify error for %s: %s", username, e)
        return None

    role = entry.get("role", "viewer")
    if role not in ALLOWED_ROLES:
        logger.warning("User %s: unknown role %r → viewer", username, role)
        role = "viewer"

    personal_path = entry.get("personal_path")
    if not personal_path:
        personal_path = default_personal_path(username)

    return UserRecord(
        username=username,
        role=role,
        personal_path=Path(personal_path),
        is_demo=bool(entry.get("is_demo", False)),
        display_name=entry.get("display_name"),
        modules=_parse_modules(entry),
        memories_scope=_parse_scope(entry),
        must_change_password=bool(entry.get("must_change_password", False)),
        may_share=bool(entry.get("may_share", True)),
    )


def lookup(username: str) -> Optional[UserRecord]:
    """
    User record without password check — for contexts where the user
    is already identified elsewhere (e.g. share owner whose personal
    path is needed to deliver the shared media).
    """
    users = _load()
    entry = users.get(username)
    if entry is None:
        return None

    role = entry.get("role", "viewer")
    if role not in ALLOWED_ROLES:
        role = "viewer"

    personal_path = entry.get("personal_path") or default_personal_path(username)

    return UserRecord(
        username=username,
        role=role,
        personal_path=Path(personal_path),
        is_demo=bool(entry.get("is_demo", False)),
        display_name=entry.get("display_name"),
        modules=_parse_modules(entry),
        memories_scope=_parse_scope(entry),
        must_change_password=bool(entry.get("must_change_password", False)),
        may_share=bool(entry.get("may_share", True)),
    )


def list_users() -> Dict[str, str]:
    """{username: role} aller Konten (ohne Demo) — für die gezielte
    Benachrichtigungs-Zustellung (notify_many: „nur Admins",
    „alle außer Verursacher"). 09/2026."""
    return {
        name: (entry.get("role") if entry.get("role") in ALLOWED_ROLES else "viewer")
        for name, entry in _load().items()
        if not entry.get("is_demo")
    }


# ─────────────────────────────────────────────────────────────
# Write path (only used by scripts/manage_users.py)
# ─────────────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    """Generate Argon2id hash. CLI-only."""
    return _ph.hash(password)


def set_must_change_password(username: str, flag: bool) -> bool:
    """Erstanmelde-Flag setzen/löschen (04.09.2026). False = Feld wird
    entfernt statt auf false gesetzt — users.json bleibt schlank.
    Returns False bei unbekanntem Nutzer."""
    data = load_raw()
    entry = (data.get("users") or {}).get(username)
    if entry is None:
        return False
    if flag:
        entry["must_change_password"] = True
    else:
        entry.pop("must_change_password", None)
    save_raw(data)
    return True


def change_password(username: str, new_password: str) -> bool:
    """Neues Passwort setzen und das Zwangswechsel-Flag löschen
    (Passt-Prüfung des alten Passworts macht der Aufrufer via
    authenticate()). Returns False bei unbekanntem Nutzer."""
    data = load_raw()
    entry = (data.get("users") or {}).get(username)
    if entry is None:
        return False
    entry["pw_hash"] = hash_password(new_password)
    entry.pop("must_change_password", None)
    save_raw(data)
    return True


def load_raw() -> dict:
    """Load full users.json (for CLI)."""
    path = USERS_JSON_PATH
    if not path.exists():
        return {"version": SCHEMA_VERSION, "users": {}}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_raw(data: dict) -> None:
    """Write users.json atomically. Mode 0600."""
    path = USERS_JSON_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    tmp.chmod(0o600)
    tmp.replace(path)
    global _cache_mtime
    with _lock:
        _cache_mtime = 0.0  # cache invalidation on next load
