"""
core/settings.py
Instance-wide settings (admin-managed).

Unlike `users.json` (identity) and `mpd.env` (infrastructure), feature
toggles live in their own JSON file under `/data/mpd-settings.json`.
Written atomically, read via mtime cache — same pattern as `userdb`.

Keys:
  - share_enabled         : bool  — share feature globally on
  - demo_enabled          : bool  — /demo-login route active
  - memories_push_enabled : bool  — tägliche Memories-Benachrichtigung (In-App) an
  - companion_provider    : str   — active AI provider (anthropic/groq/mistral/gemini)
  - companion_api_key     : str   — API key (sensitive, excluded from public_settings)
  - smtp_host/_port/_user/_pass/_from_addr/_from_name — Mailversand

**Zum SMTP-Block:** Er stand bisher nur in `mpd.env`, war damit fuer jeden
ohne Shell unerreichbar — auf fremder NAS also gar nicht einzurichten.
Jetzt gilt: **die Einstellung gewinnt, `mpd.env` ist der Rueckfall.** Ein
LEERER Wert (bzw. Port 0) heisst "nicht gesetzt" und faellt auf die
Umgebung zurueck. Bestehende Installationen, die nur `mpd.env` benutzen,
verhalten sich dadurch unveraendert.

Unknown keys in the file are ignored, missing ones fall back to default —
enables rolling upgrades without migrating the file.
"""

import json
import logging
import threading
from pathlib import Path
from typing import Any, Dict

from config import SESSION_DB_PATH

logger = logging.getLogger(__name__)

# Lives next to the sessions DB on the same persistent volume.
_SETTINGS_PATH: Path = SESSION_DB_PATH.parent / "mpd-settings.json"

# Central defaults table. Declare key + type + default here;
# everything else (read, write, validation) draws from this.
_DEFAULTS: Dict[str, Any] = {
    "share_enabled"        : True,
    "demo_enabled"         : True,
    "memories_push_enabled": True,
    "companion_provider"   : "anthropic",
    "companion_api_key"    : "",
    # Mailversand. "" bzw. 0 = nicht gesetzt → Rueckfall auf mpd.env.
    "smtp_host"            : "",
    "smtp_port"            : 0,
    "smtp_user"            : "",
    "smtp_pass"            : "",
    "smtp_from_addr"       : "",
    "smtp_from_name"       : "",
}

# Keys excluded from public_settings() — not returned to regular users via GET /api/settings.
# Nicht an gewoehnliche Nutzer. Der GANZE SMTP-Block bleibt draussen, nicht
# nur das Passwort: smtp_user ist typischerweise eine Mailadresse, und der
# Hostname verraet den Anbieter. Admins bekommen ihn ueber
# _settings_response() zurueck, das Passwort dort nur maskiert.
_SENSITIVE_KEYS: set = {
    "companion_api_key",
    "smtp_host", "smtp_port", "smtp_user", "smtp_pass",
    "smtp_from_addr", "smtp_from_name",
}

_lock = threading.Lock()
_cache: Dict[str, Any] = {}
_cache_mtime: float = 0.0


def _read_from_disk() -> Dict[str, Any]:
    path = _SETTINGS_PATH
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            logger.error("mpd-settings.json: not an object, ignored")
            return {}
        return data
    except Exception as e:
        logger.error("mpd-settings.json read failed: %s", e)
        return {}


def _ensure_fresh() -> None:
    """mtime check: re-read when file has changed."""
    global _cache, _cache_mtime
    path = _SETTINGS_PATH
    try:
        mtime = path.stat().st_mtime
    except FileNotFoundError:
        mtime = 0.0
    except Exception as e:
        logger.error("mpd-settings.json stat failed: %s", e)
        return

    with _lock:
        if mtime == _cache_mtime and _cache:
            return
        raw = _read_from_disk()
        # Only accept known keys into the cache + rough type check.
        merged = dict(_DEFAULTS)
        for k, default in _DEFAULTS.items():
            if k in raw and isinstance(raw[k], type(default)):
                merged[k] = raw[k]
        _cache = merged
        _cache_mtime = mtime


def all_settings() -> Dict[str, Any]:
    """Full settings map including sensitive keys. Internal use only."""
    _ensure_fresh()
    with _lock:
        return dict(_cache)


def public_settings() -> Dict[str, Any]:
    """Settings safe to expose to any logged-in user (sensitive keys omitted)."""
    _ensure_fresh()
    with _lock:
        return {k: v for k, v in _cache.items() if k not in _SENSITIVE_KEYS}


def get(key: str) -> Any:
    """Single value. Unknown key → ValueError so typos don't slip through."""
    if key not in _DEFAULTS:
        raise ValueError(f"Unknown settings key: {key!r}")
    _ensure_fresh()
    with _lock:
        return _cache.get(key, _DEFAULTS[key])


def set_many(updates: Dict[str, Any]) -> Dict[str, Any]:
    """
    Set multiple keys at once. Only known keys with matching type are
    accepted; unknown/mistyped values are ignored.
    Returns: current settings after the write.
    """
    global _cache, _cache_mtime
    _ensure_fresh()

    clean: Dict[str, Any] = {}
    for k, v in updates.items():
        if k not in _DEFAULTS:
            logger.warning("set_many: ignoring unknown key %r", k)
            continue
        if not isinstance(v, type(_DEFAULTS[k])):
            logger.warning("set_many: type mismatch for %r: %r", k, v)
            continue
        clean[k] = v

    if not clean:
        return all_settings()

    path = _SETTINGS_PATH
    with _lock:
        current = dict(_cache)
        current.update(clean)

        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(current, f, indent=2, ensure_ascii=False)
        try:
            tmp.chmod(0o600)  # N4 (Audit): enthält companion_api_key
        except OSError:
            pass
        tmp.replace(path)

        _cache = current
        _cache_mtime = path.stat().st_mtime
        logger.info("Settings updated: %s", ", ".join(f"{k}={v}" for k, v in clean.items()))
        return dict(_cache)
