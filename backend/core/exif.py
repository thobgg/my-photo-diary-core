"""
core/exif.py – GPS + date from photo files
==========================================
Strategy:
  1. Parse filename   (YYYY-MM-DD_HH-MM-SS.jpg)  → preferred, stable
  2. EXIF fallback    (DateTimeOriginal)         → when filename has no date
  3. GPS from EXIF    (GPSInfo)                  → coordinates for geocoding

Dependencies: Pillow (PIL) – already available on Synology

Change v1.3:
  - img._getexif() → img.getexif() (public Pillow API since 6.0)
  - Persistent EXIF cache per album (.mpd_exif_cache.json)
    → get_photo_meta_cached() reads/writes incrementally
    → scanner only calls EXIF for new/unknown photos
"""

import re
import json
import struct
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from PIL import Image
from PIL.ExifTags import TAGS, GPSTAGS

log = logging.getLogger(__name__)

# ── Regex for filenames: 2005-03-15_08-30-15.jpg ───────────────────────────
_DATE_RE     = re.compile(r'^(\d{4})-(\d{2})-(\d{2})[_\-]')
_DATETIME_RE = re.compile(r'^(\d{4})-(\d{2})-(\d{2})[_\-](\d{2})-(\d{2})-(\d{2})')
_GPS_IFD_TAG = 0x8825   # 34853 – GPSInfo sub-IFD tag ID
_EXIF_CACHE_FILE = ".mpd_exif_cache.json"


# ═══════════════════════════════════════════════════════════════════════════
# Date
# ═══════════════════════════════════════════════════════════════════════════

def filename_datetime(filename: str) -> Optional[datetime]:
    """Voller Zeitstempel aus dem Dateinamen (2005-03-15_08-30-15.jpg) —
    die einzige Datumsquelle mit Uhrzeit. Fällt auf Mitternacht zurück,
    wenn der Name nur das Datum trägt. Genutzt von core/timeline_index."""
    m = _DATETIME_RE.match(filename)
    if m:
        try:
            return datetime(*(int(g) for g in m.groups()))
        except ValueError:
            pass
    d = _date_from_filename(filename)
    return datetime(d.year, d.month, d.day) if d else None


def photo_time(filepath) -> Optional[datetime]:
    """Aufnahmezeitpunkt MIT Uhrzeit, oder None, wenn keine Uhrzeit bekannt
    ist. Erst der Dateiname (kein Dateizugriff), sonst EXIF DateTimeOriginal
    — das oeffnet die Datei und wird deshalb nur fuer wenige Treffer
    gerufen (Memories-Rangfolge, 08.09.2026), nie im Massenscan; der
    Metadaten-Cache kennt bewusst nur das Datum."""
    filepath = Path(filepath)
    m = _DATETIME_RE.match(filepath.name)
    if m:
        try:
            return datetime(*(int(g) for g in m.groups()))
        except ValueError:
            pass
    try:
        exif = Image.open(filepath).getexif()
        for tag_id, value in (exif or {}).items():
            if TAGS.get(tag_id, tag_id) == 'DateTimeOriginal':
                return datetime.strptime(value, "%Y:%m:%d %H:%M:%S")
    except Exception as e:
        log.debug("photo_time (%s): %s", filepath.name, e)
    return None


def _date_from_filename(filename: str) -> Optional[date]:
    m = _DATE_RE.match(filename)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


# ═══════════════════════════════════════════════════════════════════════════
# GPS
# ═══════════════════════════════════════════════════════════════════════════

def _parse_gps(gps_info: dict) -> Optional[tuple]:
    """Converts raw GPSInfo data to (lat, lon) decimal degrees."""
    try:
        gps = {GPSTAGS.get(k, k): v for k, v in gps_info.items()}

        lat = _dms_to_decimal(gps['GPSLatitude'],  gps.get('GPSLatitudeRef',  'N'))
        lon = _dms_to_decimal(gps['GPSLongitude'], gps.get('GPSLongitudeRef', 'E'))

        if lat is None or lon is None:
            return None
        return (lat, lon)

    except (KeyError, TypeError, struct.error) as e:
        log.debug("GPS parse error: %s", e)
        return None


def _dms_to_decimal(dms, ref: str) -> Optional[float]:
    """
    Degrees/minutes/seconds → decimal degrees.
    Pillow returns either IFDRational tuples or float tuples.
    """
    try:
        d = float(dms[0])
        m = float(dms[1])
        s = float(dms[2])
        decimal = d + m / 60 + s / 3600
        if ref in ('S', 'W'):
            decimal = -decimal
        return round(decimal, 7)
    except (IndexError, TypeError, ZeroDivisionError):
        return None


# ═══════════════════════════════════════════════════════════════════════════
# Combined: date + GPS in one call
# ═══════════════════════════════════════════════════════════════════════════

def get_photo_meta(filepath) -> dict:
    """
    Returns a dict:
    {
        "date":  date(2005, 3, 15),   # or None
        "gps":   (48.2, 16.37),       # or None
    }
    One call, one Image.open() – more efficient than two separate calls.
    """
    filepath = Path(filepath)
    result = {"date": None, "gps": None}

    # Date from filename (no file open needed)
    result["date"] = _date_from_filename(filepath.name)

    # Open EXIF only when needed (no date from filename) or when GPS is wanted
    try:
        img  = Image.open(filepath)
        exif = img.getexif()
        if not exif:
            return result

        for tag_id, value in exif.items():
            tag = TAGS.get(tag_id, tag_id)

            if tag == 'DateTimeOriginal' and result["date"] is None:
                try:
                    result["date"] = datetime.strptime(
                        value, "%Y:%m:%d %H:%M:%S"
                    ).date()
                except ValueError:
                    pass

        # GPS via sub-IFD (get_ifd is the correct way with getexif())
        gps_ifd = exif.get_ifd(_GPS_IFD_TAG)
        if gps_ifd:
            result["gps"] = _parse_gps(gps_ifd)

    except Exception as e:
        log.debug("get_photo_meta error (%s): %s", filepath.name, e)

    return result


# ═══════════════════════════════════════════════════════════════════════════
# Persistent EXIF cache per album
# ═══════════════════════════════════════════════════════════════════════════

def _cache_path(album_dir: Path) -> Path:
    return album_dir / _EXIF_CACHE_FILE


def _load_raw_cache(album_dir: Path) -> dict:
    """Reads .mpd_exif_cache.json; returns empty dict on error."""
    p = _cache_path(album_dir)
    if not p.exists():
        return {}
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log.debug("EXIF cache read error (%s): %s", album_dir.name, e)
        return {}


def _save_cache(album_dir: Path, cache: dict):
    """Writes .mpd_exif_cache.json atomically."""
    import os, tempfile
    p = _cache_path(album_dir)
    try:
        fd, tmp = tempfile.mkstemp(dir=album_dir, prefix=".exif_cache_tmp.", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False)
        os.replace(tmp, p)
    except Exception as e:
        log.warning("EXIF cache write error (%s): %s", album_dir.name, e)
        try:
            os.unlink(tmp)
        except Exception:
            pass


def invalidate_exif_cache(album_dir: Path, filename: str | None = None) -> None:
    """Drop one filename from .mpd_exif_cache.json (or the whole file if filename is None).

    Why: the cache is keyed by filename only and never re-validated against
    mtime. After EXIF writes (datetime/GPS) the on-disk file has changed but
    the cache would keep returning stale values.
    """
    p = _cache_path(album_dir)
    if not p.exists():
        return
    if filename is None:
        try:
            p.unlink()
        except OSError as e:
            log.debug("EXIF cache delete error (%s): %s", album_dir.name, e)
        return
    cache = _load_raw_cache(album_dir)
    if filename in cache:
        del cache[filename]
        _save_cache(album_dir, cache)


def get_photo_meta_cached(filepath: Path, album_dir: Path) -> dict:
    """
    Like get_photo_meta(), but with a persistent cache per album.

    Flow:
      1. Read .mpd_exif_cache.json (only expensive on the first call per album_dir)
      2. Filename in cache? → return immediately, no file open
      3. Not in cache? → call get_photo_meta(), update cache, save

    The cache dict is held in process RAM (module level) and loaded
    from disk once on the first cache miss per album.
    """
    filename = filepath.name
    cache    = _load_raw_cache(album_dir)

    if filename in cache:
        entry = cache[filename]
        d     = entry.get("date")
        g     = entry.get("gps")
        return {
            "date": date.fromisoformat(d) if d else None,
            "gps" : tuple(g) if g else None,
        }

    # Cache miss → read EXIF
    meta = get_photo_meta(filepath)

    # Serialize result (date → ISO string, gps → list)
    cache[filename] = {
        "date": meta["date"].isoformat() if meta["date"] else None,
        "gps" : list(meta["gps"])        if meta["gps"]  else None,
    }
    _save_cache(album_dir, cache)
    log.debug("EXIF cache miss+update: %s/%s", album_dir.name, filename)
    return meta


# ═══════════════════════════════════════════════════════════════════════════
# CLI test  (python3 core/exif.py <file.jpg>)
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python3 exif.py <foto.jpg>")
        sys.exit(1)

    path = Path(sys.argv[1])
    meta = get_photo_meta(path)

    print(f"File : {path.name}")
    print(f"Date : {meta['date']}")
    print(f"GPS  : {meta['gps']}")
