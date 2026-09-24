"""
core/geocoder.py – reverse geocoding via Nominatim (OpenStreetMap)
============================================================================
Coordinates (lat, lon) → city name

- No API key needed
- No Google
- RAM cache: coordinates never change → resolving once is enough
- Rate limit: 1 request/second (Nominatim fair-use policy)
- Rounds to 3 decimal places before cache lookup (~100 m precision is enough)

Dependencies: requests (already in requirements.txt)
"""

import time
import logging
from typing import Optional

import requests

log = logging.getLogger(__name__)

# ── Nominatim ───────────────────────────────────────────────────────────────
_NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
_HEADERS       = {"User-Agent": "MyPhotoDiary/1.0 (self-hosted, private use)"}
_TIMEOUT       = 10  # seconds
_MIN_INTERVAL  = 1.1 # seconds between requests (fair use: max 1/s)

# ── Cache ────────────────────────────────────────────────────────────────────
# Key: "lat,lon" (rounded to 3 decimal places)
# Value: city name (str) or "" if not found
_cache: dict = {}
_last_request: float = 0.0


def get_location(lat: float, lon: float) -> Optional[str]:
    """
    Returns city name or None on no result / error.

    Example:
        get_location(47.4179, 10.3408) → "Oberstdorf"
    """
    if lat is None or lon is None:
        return None

    cache_key = f"{round(lat, 3)},{round(lon, 3)}"

    # Cache hit
    if cache_key in _cache:
        result = _cache[cache_key]
        log.debug("Geocoder cache hit: %s → %s", cache_key, result)
        return result or None

    # Rate limiting
    _wait_for_rate_limit()

    # API request
    try:
        resp = requests.get(
            _NOMINATIM_URL,
            params={
                "lat"           : lat,
                "lon"           : lon,
                "format"        : "json",
                "zoom"          : 10,   # city level
                "addressdetails": 1,
            },
            headers=_HEADERS,
            timeout=_TIMEOUT
        )
        resp.raise_for_status()
        data = resp.json()

        name = _extract_city(data)
        _cache[cache_key] = name or ""
        log.info("Geocoder: (%.4f, %.4f) → %s", lat, lon, name)
        return name

    except requests.exceptions.Timeout:
        log.warning("Geocoder Timeout: (%.4f, %.4f)", lat, lon)
        return None
    except requests.exceptions.ConnectionError:
        log.warning("Geocoder not reachable (no internet?)")
        return None
    except Exception as e:
        log.warning("Geocoder error: %s", e)
        return None


def _extract_city(data: dict) -> Optional[str]:
    """
    Extracts the most relevant place name from the Nominatim response.
    Priority: city → town → village → municipality → county → state
    """
    if not data:
        return None

    address = data.get("address", {})

    for field in ("city", "town", "village", "municipality",
                  "suburb", "county", "state"):
        value = address.get(field)
        if value:
            return value

    # Fallback: first component of display_name
    display = data.get("display_name", "")
    if display:
        return display.split(",")[0].strip()

    return None


def _wait_for_rate_limit():
    """Ensures at least _MIN_INTERVAL seconds since the last request."""
    global _last_request
    elapsed = time.time() - _last_request
    if elapsed < _MIN_INTERVAL:
        time.sleep(_MIN_INTERVAL - elapsed)
    _last_request = time.time()


def cache_size() -> int:
    """Count of cached coordinates – for logging/debug."""
    return len(_cache)


# ═══════════════════════════════════════════════════════════════════════════
# CLI test  (python3 core/geocoder.py)
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)

    # Coordinates from argument, or example fallback (Oberstdorf)
    if len(sys.argv) == 3:
        lat, lon = float(sys.argv[1]), float(sys.argv[2])
    else:
        lat, lon = 47.4179829, 10.3408813
        print(f"No coordinates given – testing with Oberstdorf ({lat}, {lon})\n")

    print(f"Coordinates : {lat}, {lon}")
    result = get_location(lat, lon)
    print(f"Location    : {result}")
    print(f"Cache size  : {cache_size()}")

    # Second call: must be served from cache
    print("\nSecond call (from cache):")
    result2 = get_location(lat, lon)
    print(f"Location    : {result2}")
