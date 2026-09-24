"""
geo_reverse.py — City-level reverse geocoding for the lightbox topbar

Backed by Nominatim (OpenStreetMap) via core/geocoder.py, which already
ships with RAM cache + 1.1s rate limit per Nominatim's fair-use policy.

This module adds:
- Filesystem cache (survives restarts; Nominatim's RAM cache does not)
- Negative caching (empty result is stored too — same lookup won't re-ask)
- Coordinate rounding to 3 decimals (~100 m grid — photos in same area share)

The api_key parameter is kept for backwards compatibility with the router
signature but is ignored — Nominatim needs no key.
"""

import json
import logging
import os
import tempfile
import threading
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── Tunables ────────────────────────────────────────────────────────────────
# v4 cache path: bumped after switching from Maptiler to Nominatim — old
# entries had wrong-class results (street/locality instead of city).
_CACHE_PATH    = Path("/data/geo_reverse_cache_v4.json")
_KEY_DECIMALS  = 3       # ~100 m grid — photos in same area share cache hits

# ── State (module-level, all access through _lock) ──────────────────────────
_lock  = threading.Lock()
_cache = None        # type: Optional[dict]


# ── Cache I/O ───────────────────────────────────────────────────────────────
def _key(lat: float, lon: float) -> str:
    return f"{round(lat, _KEY_DECIMALS)},{round(lon, _KEY_DECIMALS)}"


def _load_cache() -> dict:
    if not _CACHE_PATH.exists():
        return {}
    try:
        with open(_CACHE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning("geo_reverse cache read failed: %s", e)
        return {}


def _save_cache(cache: dict) -> None:
    try:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=_CACHE_PATH.parent, prefix=".geo_rev_tmp.", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, _CACHE_PATH)
    except Exception as e:
        logger.warning("geo_reverse cache write failed: %s", e)


# ── Public ──────────────────────────────────────────────────────────────────
def reverse_city(lat: float, lon: float, _api_key: Optional[str] = None,
                 _language: str = "de") -> Optional[str]:
    """
    Cached city-level reverse geocoding via Nominatim.
    Returns city name or None on no result / error.
    Negative results cached (empty string in store) so the same lookup
    won't repeatedly hit Nominatim.

    `_api_key` and `_language` are ignored — kept for router compatibility.
    """
    if lat is None or lon is None:
        return None

    global _cache
    k = _key(lat, lon)

    # Phase 1: FS-cache check inside lock
    with _lock:
        if _cache is None:
            _cache = _load_cache()
        if k in _cache:
            cached = _cache[k]
            logger.debug("geo_reverse cache hit %s → %r", k, cached)
            return cached or None

    # Phase 2: ask Nominatim — outside our lock (geocoder has its own rate
    # limit and serializes itself; holding our lock would needlessly stall
    # parallel cache hits for other coords)
    try:
        from core.geocoder import get_location
        name = get_location(lat, lon)
    except Exception as e:
        logger.warning("Nominatim reverse error (%.4f,%.4f): %s", lat, lon, e)
        name = None

    # Phase 3: persist (positive or negative) inside lock
    with _lock:
        if _cache is None:
            _cache = {}
        _cache[k] = name or ""
        _save_cache(_cache)

    logger.info("geo_reverse Nominatim (%.4f,%.4f) → %r", lat, lon, name)
    return name

